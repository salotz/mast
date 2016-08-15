"""The PiStacking module defines the PiStackingType and
PiStackingInx for explicit hydrogens.

"""


import itertools as it
from collections import namedtuple

import numpy as np
import numpy.linalg as la

import mast.config.interactions as mastinxconfig
from mast.interactions.interactions import InteractionType, Interaction, InteractionError

class PiStackingType(InteractionType):
    """Defines an InteractionType class for hydrogen bonds between members
    with explicit hydrogens.

    """

    attributes = {}
    interaction_name = "PiStacking"
    feature_keywords = mastinxconfig.PISTACKING_FEATURES
    grouping_attribute = 'rdkit_family'

    def __init__(self, pi_stacking_type_name,
                 feature_types=None,
                 association_type=None,
                 assoc_member_pair_idxs=None,
                 **pi_stacking_attrs):

        super().__init__(pi_stacking_type_name,
                         feature_types=feature_types,
                         association_type=association_type,
                         assoc_member_pair_idxs=assoc_member_pair_idxs,
                         **pi_stacking_attrs)

    @classmethod
    def find_hits(cls, member_a, member_b):

        # check that the keys ar okay in parent class
        # super().find_hits(members_features)

        # for each member collect the grouped features
        # initialize list of members
        members_features = [{'donors':[], 'acceptors':[]} for member in [member_a, member_b]]
        for i, member in enumerate([member_a, member_b]):
            for feature_key, feature in member.features.items():
                # get groupby attribute to use as a key
                group_attribute = feature.feature_type.attributes_data[cls.grouping_attribute]

                if group_attribute == cls.acceptor_key:
                    acceptor_tup = (feature_key, feature)
                    members_features[i]['acceptors'].append(acceptor_tup)

                elif group_attribute == cls.donor_key:
                    # get the donor-H pairs of atoms for this donor
                    donor_atom = feature.atoms[0]
                    donor_H_pairs = [(feature, atom) for atom in
                                     donor_atom.adjacent_atoms if
                                     atom.atom_type.element == 'H']
                    donor_H_pairs_tup = [(feature_key, donor_H_pair) for
                                         donor_H_pair in donor_H_pairs]
                    members_features[i]['donors'].extend(donor_H_pairs_tup)

        donor_acceptor_pairs = []
        # pair the donors from the first with acceptors of the second
        donor_acceptor_pairs.extend(it.product(members_features[0]['donors'],
                                               members_features[1]['acceptors']))
        # pair the acceptors from the first with the donors of the second
        donor_acceptor_pairs.extend(it.product(members_features[1]['donors'],
                                               members_features[0]['acceptors']))

        # scan the pairs for hits
        hit_pair_keys = []
        hbonds = []
        for donor_tup, acceptor_tup in donor_acceptor_pairs:
            donor_feature_key = donor_tup[0]
            donor_feature = donor_tup[1][0]
            h_atom = donor_tup[1][1]
            acceptor_feature_key = acceptor_tup[0]
            acceptor_feature = acceptor_tup[1]
            # try to make a PiStackingInx object, which calls check,
            #
            # OPTIMIZATION: otherwise we have to call check first then
            # the PiStackingInx constructor will re-call check to
            # get the angle and distance. If we allow passing and not
            # checking the angle and distance in the constructor then
            # it would be faster, however I am not going to allow that
            # in this 'safe' InteractionType, an unsafe optimized
            # version can be made separately if desired.
            try:
                hbond = PiStackingInx(donor=donor_feature, H=h_atom,
                                        acceptor=acceptor_feature)
            # else continue to the next pairing
            except InteractionError:
                continue
            # if it succeeds add it to the list of H-Bonds
            hbonds.append(hbond)
            # and the feature keys to the feature key pairs
            hit_pair_keys.append((donor_feature_key, acceptor_feature_key))

        return hit_pair_keys, hbonds

    @classmethod
    def check(cls, donor_atom, h_atom, acceptor_atom):
        """Checks if the 3 atoms qualify as a hydrogen bond. Returns a tuple
        (bool, float, float) where the first element is whether or not it
        qualified, the second and third are the distance and angle
        respectively. Angle may be None if distance failed to qualify.

        """
        from scipy.spatial.distance import cdist
        distance = cdist(np.array([donor_atom.coords]), np.array([acceptor_atom.coords]))[0,0]
        if cls.check_distance(distance) is False:
            return (False, distance, None)

        v1 = donor_atom.coords - h_atom.coords
        v2 = acceptor_atom.coords - h_atom.coords
        try:
            angle = np.degrees(np.arccos(np.dot(v1, v2)/(la.norm(v1) * la.norm(v2))))
        except RuntimeWarning:
            print("v1: {0} \n"
                  "v2: {1}".format(v1, v2))
        if cls.check_angle(angle) is False:
            return (False, distance, angle)

        return (True, distance, angle)

    @classmethod
    def check_distance(cls, distance):
        """For a float distance checks if it is less than the configuration
        file HBOND_DIST_MAX value.

        """
        if distance < mastinxconfig.HBOND_DIST_MAX:
            return True
        else:
            return False

    @classmethod
    def check_angle(cls, angle):
        """For a float distance checks if it is less than the configuration
        file HBOND_DON_ANGLE_MIN value.

        """

        if angle > mastinxconfig.HBOND_DON_ANGLE_MIN:
            return True
        else:
            return False

    @property
    def record(self):
        record_fields = ['interaction_class', 'interaction_type',
                         'association_type', 'assoc_member_pair_idxs',
                         'donor_feature_type', 'acceptor_feature_type'] + \
                         list(self.attributes_data.keys())
        PiStackingTypeRecord = namedtuple('PiStackingTypeRecord', record_fields)
        record_attr = {'interaction_class' : self.name}
        record_attr['interaction_type'] = self.interaction_name
        record_attr['association_type'] = self.association_type.name
        record_attr['assoc_member_pair_idxs'] = self.assoc_member_pair_idxs
        record_attr['donor_feature_type'] = self.feature_types[0]
        record_attr['acceptor_feature_type'] = self.feature_types[1]

        return PiStackingTypeRecord(**record_attr)

    def pdb_serial_output(self, inxs, path, delim=","):
        """Output the pdb serial numbers (index in pdb) of the donor and
        acceptor in each HBond to a file like:

        donor_1, acceptor_1
        donor_2, acceptor_2
        ...

        """

        with open(path, 'w') as wf:
            for inx in inxs:
                wf.write("{0}{1}{2}\n".format(inx.donor.atom_type.pdb_serial_number,
                                              delim,
                                              inx.acceptor.atom_type.pdb_serial_number))


class PiStackingInx(Interaction):
    """Substantiates PiStackingType by selecting donor and acceptor
    features, as well as the involved Hydrogen atom.

    """

    def __init__(self, donor=None, H=None, acceptor=None):

        donor_atom = donor.atoms[0]
        acceptor_atom = acceptor.atoms[0]
        okay, distance, angle = PiStackingType.check(donor_atom, H, acceptor_atom)
        if not okay:
            if angle is None:
                raise InteractionError(
                    """donor: {0}
                    H: {1}
                    acceptor: {2}
                    distance = {3} FAILED
                    angle = not calculated""".format(donor_atom, H, acceptor_atom, distance))

            else:
                raise InteractionError(
                    """donor: {0}
                    H: {1}
                    acceptor: {2}
                    distance = {3}
                    angle = {4} FAILED""".format(donor_atom, H, acceptor_atom, distance, angle))

        # success, finish creating interaction
        atom_system = donor.system
        super().__init__(features=[donor, acceptor],
                         interaction_type=PiStackingType,
                         system=atom_system)
        self._donor = donor
        self._H = H
        self._acceptor = acceptor
        self._distance = distance
        self._angle = angle

    @property
    def donor(self):
        """The donor Feature in the hydrogen bond."""
        return self._donor

    @property
    def H(self):
        """The donated hydrogen Atom in the hydrogen bond."""
        return self._H

    @property
    def acceptor(self):
        """The acceptor Feature in the hydrogen bond."""
        return self._acceptor

    @property
    def distance(self):
        """The distance between the donor atom and the acceptor atom."""
        return self._distance

    @property
    def angle(self):
        """The angle (in degrees) between the donor atom, hydrogen atom, and
        acceptor atom with the hydrogen atom as the vertex.

        """
        return self._angle

    @property
    def record(self):
        record_fields = ['interaction_class',
                         'donor_coords', 'acceptor_coords',
                         'distance', 'angle',
                         'H_coords']

        PiStackingInxRecord = namedtuple('PiStackingInxRecord', record_fields)
        record_attr = {'interaction_class' : self.interaction_class.name}
        record_attr['donor_coords'] = self.donor.atoms[0].coords
        record_attr['acceptor_coords'] = self.acceptor.atoms[0].coords
        record_attr['distance'] = self.distance
        record_attr['angle'] = self.angle
        record_attr['H_coords'] = self.H.coords

        return PiStackingInxRecord(**record_attr)

    def pickle(self, path):
        import sys
        sys.setrecursionlimit(10000)
        import pickle
        with open(path, 'wb') as wf:
            pickle.dump(self, wf)

