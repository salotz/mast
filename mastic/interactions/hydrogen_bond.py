"""The HydrogenBond module defines the HydrogenBondType and
HydrogenBondInx for explicit hydrogens.

"""
import itertools as it
from collections import namedtuple, defaultdict

import numpy as np
import numpy.linalg as la
from scipy.spatial.distance import cdist
#import pandas as pd

import mastic.config.interactions as masticinxconfig
import mastic.config.features as masticfeatconfig
from mastic.interactions.interactions import InteractionType, Interaction, InteractionError

# fields expected for writing data out as results, this method will be
# improved in the future
_pdb_fields = ['pdb_name', 'pdb_residue_name', 'pdb_residue_number', 'pdb_serial_number']

def hbond_profiles_df_stats(profiles_df):
    """Given a master dataframe from multiple profilings this will give
    another dataframe of the statistics for thos frames.

    """


    import pandas as pd
    # group the hits by hbond interaction class
    hit_gb = profiles_df.groupby('hit_idx')

    # define the fields for the table
    hbond_stats_fields = ['interaction_id', 'hit_idx',
                          'distance_mean', 'distance_std',
                          'distance_min', 'distance_max',
                          'angle_mean', 'angle_std',
                          'angle_min', 'angle_max',
                          'frames',
                          'freq' ]
    hbond_stats_cols = {field : [] for field in hbond_stats_fields}

    # distance
    for hit_idx, hit_df in hit_gb:
        hbond_stats_cols['distance_mean'].append(hit_df['distance'].mean())
        hbond_stats_cols['distance_std'].append(hit_df['distance'].std())
        hbond_stats_cols['distance_min'].append(hit_df['distance'].min())
        hbond_stats_cols['distance_max'].append(hit_df['distance'].max())
        # angle
        hbond_stats_cols['angle_mean'].append(hit_df['angle'].mean())
        hbond_stats_cols['angle_std'].append(hit_df['angle'].std())
        hbond_stats_cols['angle_min'].append(hit_df['angle'].min())
        hbond_stats_cols['angle_max'].append(hit_df['angle'].max())
        # add the frames it is a part of
        hbond_stats_cols['hit_idx'].append(int(hit_idx))
        hbond_stats_cols['frames'].append(list(hit_df['profile_id']))
        hbond_stats_cols['freq'].append(hit_df.shape[0])
        hbond_stats_cols['interaction_id'].append(hit_df['interaction_class'].values[0])

    hbond_stats_df = pd.DataFrame(hbond_stats_cols, index=hbond_stats_cols['hit_idx'])
    # calculate the normalized frequencies based on the number of
    # total hbonds in the collection
    hbond_stats_df['norm_freq'] = hbond_stats_df['freq'].divide(
        hbond_stats_df[hbond_stats_df['freq'] > 0].shape[0], fill_value=0.0)

    return hbond_stats_df


class HydrogenBondType(InteractionType):
    """Defines an InteractionType class for hydrogen bonds between members
    with explicit hydrogens.

    """

    ## class attributes that need to exist
    attributes = {}
    interaction_name = "HydrogenBond"
    feature_keys = masticinxconfig.HYDROGEN_BOND_FEATURE_KEYS
    feature_classifiers = masticinxconfig.HYDROGEN_BOND_FEATURES
    # degree is the number of features that participate in an interaction
    degree = masticinxconfig.HYDROGEN_BOND_DEGREE
    commutative = masticinxconfig.HYDROGEN_BOND_COMMUTATIVITY
    interaction_param_keys = masticinxconfig.HYDROGEN_BOND_PARAM_KEYS

    ## specific to this class parameters but make defaults easier and
    ## for writing other similar InteractionTypes
    distance_cutoff = masticinxconfig.HYDROGEN_BOND_DIST_MAX
    angle_cutoff = masticinxconfig.HYDROGEN_BOND_DON_ANGLE_MIN

    ## convenience class attributes particular to this class
    donor_key = feature_keys[0]
    acceptor_key = feature_keys[1]
    donor_idx = 0
    acceptor_idx = 1
    feature_type_ordering = {feature_key : i for i, feature_key in enumerate(feature_keys)}
    donor_feature_classifiers = feature_classifiers[donor_key]
    acceptor_feature_classifiers = feature_classifiers[acceptor_key]


    def __init__(self, hydrogen_bond_type_name,
                 feature_types=None,
                 association_type=None,
                 assoc_member_pair_idxs=None,
                 **hydrogen_bond_attrs):

        super().__init__(hydrogen_bond_type_name,
                         feature_types=feature_types,
                         association_type=association_type,
                         assoc_member_pair_idxs=assoc_member_pair_idxs,
                         **hydrogen_bond_attrs)

        self.donor_type = feature_types[0]
        self.acceptor_type = feature_types[1]

    @staticmethod
    def interaction_constructor(*params, **kwargs):
        return HydrogenBondInx(*params, **kwargs)

    @classmethod
    def find_hits(cls, members,
                  interaction_classes=None,
                  return_feature_keys=False,
                  return_failed_hits=False):

        # TODO value checks

        # scan the pairs for hits and assign interaction classes if given
        return super().find_hits(members,
                                 interaction_classes=interaction_classes,
                                 return_feature_keys=return_feature_keys,
                                 return_failed_hits=return_failed_hits)

    @classmethod
    def check(cls, donor, acceptor):
        """Checks if the 3 atoms qualify as a hydrogen bond. Returns a tuple
        (bool, float, float) where the first element is whether or not it
        qualified, the second and third are the distance and angle
        respectively. Angle may be None if distance failed to qualify.

        Compatible with RDKit Acceptor and Donor features

        """

        # assemble the features and their tests
        features = [donor, acceptor]
        feature_tests = [cls.test_features_distance, cls.test_features_angle]
        # pass to parent function, this returns a results tuple of the
        # form: (okay, (param_values))
        return super().check(features, feature_tests)

    @classmethod
    def test_features_distance(cls, donor, acceptor):
        donor_atom = donor.atoms[0]
        acceptor_atom = acceptor.atoms[0]

        # calculate the distance
        distance = cls.calc_distance(donor_atom, acceptor_atom)

        # check it
        # if it fails return a false okay and the distance
        if cls.check_distance(distance) is False:
            return False, distance
        # otherwise return that it was okay
        else:
            return True, distance

    @classmethod
    def test_features_angle(cls, donor, acceptor):
        donor_atom = donor.atoms[0]
        acceptor_atom = acceptor.atoms[0]

        # if the distance passes we want to check the angle, which we
        # will need the coordinates of the adjacent hydrogens to the donor
        h_atoms = [atom for atom in donor_atom.adjacent_atoms
                   if atom.atom_type.element == 'H']

        # check to see if even 1 hydrogen atom satisfies the angle
        # constraint
        okay_angle = None
        h_atoms_iter = iter(h_atoms)
        h_atoms_angles = []
        # if it doesn't the loop will end and a false okay and the bad
        # angles will be returned
        while okay_angle is None:
            try:
                h_atom = next(h_atoms_iter)
            # none are found to meet the constraint
            except StopIteration:
                return False, tuple(h_atoms_angles)

            # calculate the angle for this hydrogen
            angle = cls.calc_angle(donor_atom, acceptor_atom, h_atom)

            # save the angle
            h_atoms_angles.append(angle)

            # check if the angle meets the constraints
            if cls.check_angle(angle) is True:
                okay_angle = angle

        # if it succeeds in finding a good angle return the first one
        return True, okay_angle

    #### Hydrogen Bond Specific methods
    # i.e. not necessarily found in other interaction types

    @classmethod
    def check_distance(cls, distance):
        """For a float distance checks if it is less than the configuration
        file HYDROGEN_BOND_DIST_MAX value.

        """
        if distance < cls.distance_cutoff:
            return True
        else:
            return False

    @classmethod
    def check_angle(cls, angle):
        """For a float distance checks if it is less than the configuration
        file HYDROGEN_BOND_DON_ANGLE_MIN value.

        """

        if angle > cls.angle_cutoff:
            return True
        else:
            return False

    @classmethod
    def is_donor(cls, feature):
        if feature.attributes_data[cls.grouping_attribute] in cls.donor_keys:
            return True
        else:
            return False

    @classmethod
    def is_acceptor(cls, feature):
        if feature.attributes_data[cls.grouping_attribute] == cls.acceptor_key:
            return True
        else:
            return False

    @staticmethod
    def calc_distance(donor_atom, acceptor_atom):
        return cdist(np.array([donor_atom.coords]), np.array([acceptor_atom.coords]))[0,0]

    @staticmethod
    def calc_angle(donor_atom, acceptor_atom, h_atom):
        v1 = donor_atom.coords - h_atom.coords
        v2 = acceptor_atom.coords - h_atom.coords
        return np.degrees(np.arccos(np.dot(v1, v2)/(la.norm(v1) * la.norm(v2))))

    @property
    def atom_idxs(self):
        # go through the features and return a list of the atom
        # indices for each feature
        inxclass_atom_idxs = []
        for feature_type in self.feature_types:
            inxclass_atom_idxs.append(feature_type.atom_idxs)

        return inxclass_atom_idxs

    @property
    def feature_atom_types(self, feat_idx):
        return self.feature_types[feat_idx].atom_types

    def feature_atoms_pdb_data(self, feat_idx):
        fields_data = {}
        for field in _pdb_fields:
            atoms_field = []
            for atom_idx, atom_type in enumerate(self.feature_types[feat_idx].atom_types):
                atoms_field.append(atom_type.attributes_data[field])
            fields_data[field] = atoms_field

        return fields_data

    @property
    def record_dict(self):
        record_attr = {}
        #record_attr = {'interaction_class' : self.name}
        #record_attr['interaction_type'] = self.interaction_name
        #record_attr['association_type'] = self.association_type.name
        #record_attr['donor_feature_type'] = self.feature_types[0].name
        #record_attr['acceptor_feature_type'] = self.feature_types[1].name

        record_attr['assoc_member_pair_idxs'] = self.assoc_member_pair_idxs
        record_attr['donor_member_idx'] = self.assoc_member_pair_idxs[self.donor_idx]
        record_attr['acceptor_member_idx'] = self.assoc_member_pair_idxs[self.acceptor_idx]
        record_attr['donor_atom_idxs'] = self.atom_idxs[self.donor_idx]
        record_attr['acceptor_atom_idxs'] = self.atom_idxs[self.acceptor_idx]
        for feature_key in self.feature_keys:
            feat_idx = self.feature_type_ordering[feature_key]
            for field_name, atom_values in self.feature_atoms_pdb_data(feat_idx).items():
                record_attr["{}_{}".format(feature_key, field_name)] = tuple(atom_values)

        return record_attr

    @property
    def record(self):
        return HydrogenBondTypeRecord(self.record_dict)

    def pdb_serial_output(self, inxs, path, delim=","):
        """Output the pdb serial numbers (index in pdb) of the donor and
        acceptor in each HBond to a file like:

        donor_1, acceptor_1
        donor_2, acceptor_2
        ...

        Notice: This will probably be removed in the future.

        """

        with open(path, 'w') as wf:
            for inx in inxs:
                wf.write("{0}{1}{2}\n".format(inx.donor.atom_type.pdb_serial_number,
                                              delim,
                                              inx.acceptor.atom_type.pdb_serial_number))

# HydrogenBondTypeRecord

_hydrogen_bond_type_record_fields =['assoc_member_pair_idxs',
                                    'donor_member_idx', 'acceptor_member_idx',
                                    'donor_atom_idxs', 'acceptor_atom_idxs'] + \
                            ["{}_{}".format('donor', field_name) for field_name in _pdb_fields] +\
                            ["{}_{}".format('acceptor', field_name) for field_name in _pdb_fields]


# _hydrogen_bond_type_record_fields = ['interaction_class', 'interaction_type',
#                                      'association_type', 'assoc_member_pair_idxs',
#                                      'donor_feature_type', 'acceptor_feature_type']
HydrogenBondTypeRecord = namedtuple('HydrogenBondTypeRecord', _hydrogen_bond_type_record_fields)

class HydrogenBondInx(Interaction):
    """Substantiates HydrogenBondType by selecting donor and acceptor
    features, as well as the involved Hydrogen atom.

    """

    interaction_type = HydrogenBondType

    def __init__(self, donor, acceptor,
                 check=True,
                 interaction_class=None,
                 **param_values):

        donor_atom = donor.atoms[0]
        acceptor_atom = acceptor.atoms[0]
        if check:
            okay, param_values = self.interaction_type.check(donor_atom, acceptor_atom)

            if not okay:
                raise InteractionError

        # success, finish creating interaction
        atom_system = donor.system
        super().__init__(features=[donor, acceptor],
                         system=atom_system,
                         interaction_class=interaction_class,
                         **param_values)
        self._donor = donor
        self._acceptor = acceptor

    @property
    def donor(self):
        """The donor Feature in the hydrogen bond."""
        return self._donor

    # TODO implement a way to find all the H atoms that satisfy the interaction
    @property
    def H(self):
        """The donated hydrogen Atom in the hydrogen bond."""
        raise NotImplementedError
        # return self._H

    @property
    def acceptor(self):
        """The acceptor Feature in the hydrogen bond."""
        return self._acceptor

    @property
    def record_dict(self):
        record_attr = {}
        record_attr['donor_coords'] = tuple(self.donor.atoms[0].coords)
        record_attr['acceptor_coords'] = tuple(self.acceptor.atoms[0].coords)
        # TODO because the H might be ambiguous, see the H property
        # record_attr['H_coords'] = self.H.coords
        record_attr.update(self.interaction_params)
        record_attr.update(self.interaction_class.record_dict)

        return record_attr

    @property
    def record(self):
        return HydrogenBondInxRecord(self.record_dict)

# HydrogenBondInxRecord
_hydrogen_bond_inx_record_fields = ['donor_coords', 'acceptor_coords'] + \
                                    HydrogenBondType.interaction_param_keys
                                    #'H_coords']
HydrogenBondInxRecord = namedtuple('HydrogenBondInxRecord', _hydrogen_bond_inx_record_fields)



# def pp(self):

#     string = ("{self_str}\n"
#               "donor: {donor_el}\n"
#               "       coords = {donor_coords}\n"
#               "       pdb_serial = {donor_serial}\n"
#               "       pdb_residue_name = {donor_resname}\n"
#               "       pdb_residue_index = {donor_resnum}\n"
#               "acceptor: {acceptor_el}\n"
#               "          coords = {acceptor_coords}\n"
#               "          pdb_serial = {acceptor_serial}\n"
#               "          pdb_residue_name = {acceptor_resname}\n"
#               "          pdb_residue_index = {acceptor_resnum}\n"
#               "distance: {distance}\n"
#               "angle: {angle}\n").format(
#                   self_str=str(self),
#                   donor_el=self.donor.atom_type.element,
#                   donor_coords=tuple(self.donor.coords),
#                   donor_mol_name=self.donor.molecule.molecule_type.name,
#                   donor_serial=self.donor.atom_type.pdb_serial_number,
#                   donor_resname=self.donor.atom_type.pdb_residue_name,
#                   donor_resnum=self.donor.atom_type.pdb_residue_number,
#                   acceptor_el=self.acceptor.atom_type.element,
#                   acceptor_coords=tuple(self.acceptor.coords),
#                   acceptor_mol_name=self.acceptor.molecule.molecule_type.name,
#                   acceptor_serial=self.acceptor.atom_type.pdb_serial_number,
#                   acceptor_resname=self.acceptor.atom_type.pdb_residue_name,
#                   acceptor_resnum=self.acceptor.atom_type.pdb_residue_number,
#                   distance=self.distance,
#                   angle=self.angle,)

#     print(string)
