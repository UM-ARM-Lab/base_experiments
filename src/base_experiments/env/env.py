import abc
import functools
import typing

import numpy as np
import torch
from arm_pytorch_utilities import load_data as load_utils, array_utils
from arm_pytorch_utilities.make_data import datasource

from base_experiments import cfg
from pytorch_volumetric.model_to_sdf import aabb_to_ordered_end_points
from stucco.detection import ContactDetector


class InfoKeys:
    OBJ_POSES = "object_poses"
    DEE_IN_CONTACT = "dee in contact"
    CONTACT_ID = "contact_id"
    # highgest frequency feedback of reaction force and torque at end effector
    HIGH_FREQ_REACTION_F = "r"
    HIGH_FREQ_REACTION_T = "t"
    HIGH_FREQ_WRENCH = "w"
    HIGH_FREQ_EE_POSE = "p"  # position cat with unit quaternion orientation
    HIGH_FREQ_CONTACT_POINT = "c"
    LOW_FREQ_REACTION_F = "reaction"
    LOW_FREQ_REACTION_T = "torque"


class TrajectoryLoader(load_utils.DataLoader):
    def __init__(self, *args, file_cfg=cfg, ignore_masks=False, **kwargs):
        self.info_desc = {}
        self.ignore_masks = ignore_masks
        super().__init__(file_cfg, *args, **kwargs)

    @staticmethod
    @abc.abstractmethod
    def _info_names():
        return []

    def _apply_masks(self, d, x, y):
        """Handle common logic regardless of x and y"""
        info_index_offset = 0
        info = []
        for name in self._info_names():
            if name in d:
                info.append(d[name][1:])
                dim = info[-1].shape[1]
                self.info_desc[name] = slice(info_index_offset, info_index_offset + dim)
                info_index_offset += dim

        mask = d['mask']
        # add information about env/groups of data (different simulation runs are contiguous blocks)
        groups = array_utils.discrete_array_to_value_ranges(mask)
        envs = np.zeros(mask.shape[0])
        current_env = 0
        for v, start, end in groups:
            if v == 0:
                continue
            envs[start:end + 1] = current_env
            current_env += 1
        # throw away first element as always
        envs = envs[1:]
        info.append(envs)
        self.info_desc['envs'] = slice(info_index_offset, info_index_offset + 1)
        info = np.column_stack(info)

        u = d['U'][:-1]
        # potentially many trajectories, get rid of buffer state in between

        x = x[:-1]
        xu = np.column_stack((x, u))

        # pack expanded pxu into input if config allows (has to be done before masks)
        # otherwise would use cross-file data)
        if self.config.expanded_input:
            # move y down 1 row (first element can't be used)
            # (xu, pxu)
            xu = np.column_stack((xu[1:], xu[:-1]))
            y = y[1:]
            info = info[1:]

            mask = mask[1:-1]
        else:
            mask = mask[:-1]

        mask = mask.reshape(-1) != 0

        # might want to ignore masks if we need all data points
        if not self.ignore_masks:
            xu = xu[mask]
            info = info[mask]
            y = y[mask]

        self.config.load_data_info(x, u, y, xu)
        return xu, y, info


class Visualizer:
    """Common interface for drawing environment elements"""
    # many times when drawing mesh we only need 1 mesh per name
    # in these cases it's more convenient for the drawer to bookkeep the mesh's ID
    # this is a special value for the object_id input of draw_mesh that will handle this case
    USE_DEFAULT_ID_FOR_NAME = -100

    def set_hide_text(self, hide_text):
        pass

    @abc.abstractmethod
    def draw_point(self, name, point, color=(0, 0, 0), length=0.01, length_ratio=1, rot=0, height=None, label=None,
                   scale=2):
        pass

    def draw_points(self, name, points, color=(0, 0, 0), **kwargs):
        for i, point in enumerate(points):
            self.draw_point(f"{name}.{i}", point, color[i] if len(color) > 3 else color, **kwargs)

    @abc.abstractmethod
    def draw_2d_pose(self, name, pose, color=(0, 0, 0), length=0.15 / 2, height=None):
        pass

    @abc.abstractmethod
    def draw_2d_line(self, name, start, diff, color=(0, 0, 0), size=2., scale=0.4):
        pass

    def draw_2d_lines(self, name, starts, diffs, color=(0, 0, 0), **kwargs):
        for i in range(len(starts)):
            self.draw_2d_line(f"{name}.{i}", starts[i], diffs[i], color[i] if len(color) > 3 else color, **kwargs)

    @abc.abstractmethod
    def clear_visualizations(self, names=None):
        pass

    @abc.abstractmethod
    def clear_visualization_after(self, prefix, index):
        pass

    @abc.abstractmethod
    def draw_transition(self, x, new_x):
        pass

    @abc.abstractmethod
    def draw_mesh(self, name, model, pose, rgba=(0, 0, 0, 1.), scale=1., object_id=None, vis_frame_pos=(0, 0, 0),
                  vis_frame_rot=(0, 0, 0, 1)):
        """
        :param name: name for group of mesh markers
        :param model: mesh resource file (e.g. mesh.obj)
        :param pose: (position, xyzw unit quaternion)
        :param rgba:
        :param scale:
        :param object_id: ID for the object for redrawing; pass back in to change its pose instead of drawing a new
            mesh
        :param vis_frame_pos position of the visual frame wrt the object frame
        :param vis_frame_rot xyzw unit quaternion of the visual frame wrt the object frame
        :return: object ID for the object drawn; will be the same as the input one if it's non-default
        """
        pass


class Env:
    @property
    @abc.abstractmethod
    def nx(self):
        """Dimensionality of state space"""
        return 0

    @property
    @abc.abstractmethod
    def nu(self):
        """Dimensionality of action space"""
        return 0

    @staticmethod
    @abc.abstractmethod
    def state_names():
        """Get list of names, one for each state corresponding to the index"""
        return []

    @classmethod
    @abc.abstractmethod
    def state_difference(cls, state, other_state):
        """Get state - other_state in state space"""
        return np.array([])

    @classmethod
    @abc.abstractmethod
    def state_distance(cls, state_difference):
        """Get a measure of distance in the state space"""
        return 0

    @classmethod
    def state_distance_two_arg(cls, state, other_state):
        return cls.state_distance(cls.state_difference(state, other_state))

    @staticmethod
    @abc.abstractmethod
    def control_names():
        return []

    @staticmethod
    @abc.abstractmethod
    def get_control_bounds():
        """Get lower and upper bounds for control"""
        return np.array([]), np.array([])

    @classmethod
    @abc.abstractmethod
    def control_similarity(cls, u1, u2):
        """Get similarity between 0 - 1 of two controls"""

    @classmethod
    @abc.abstractmethod
    def state_cost(cls):
        """Assuming cost function is xQx + uRu, return Q"""
        return np.diag([])

    @classmethod
    @abc.abstractmethod
    def control_cost(cls):
        """Assuming cost function is xQx + uRu, return R"""
        return np.diag([])

    @property
    @abc.abstractmethod
    def vis(self) -> Visualizer:
        """Return the visualizer used to render elements for this environment"""

    @abc.abstractmethod
    def create_contact_detector(self, residual_threshold, residual_precision) -> ContactDetector:
        """Create a contact detector for detecting and isolating contact"""

    @property
    @abc.abstractmethod
    def contact_detector(self) -> ContactDetector:
        """Get the contact detector"""

    def verify_dims(self):
        u_min, u_max = self.get_control_bounds()
        assert u_min.shape[0] == u_max.shape[0]
        assert u_min.shape[0] == self.nu
        assert len(self.state_names()) == self.nx
        assert len(self.control_names()) == self.nu
        assert self.state_cost().shape[0] == self.nx
        assert self.control_cost().shape[0] == self.nu

    def reset(self):
        """reset robot to init configuration"""
        pass

    @abc.abstractmethod
    def step(self, action):
        """Take an action step, returning new state, reward, done, and additional info in the style of gym envs"""
        state = np.array(self.nx)
        cost, done = self.evaluate_cost(state, action)
        info = None
        return state, -cost, done, info

    @abc.abstractmethod
    def evaluate_cost(self, state, action=None):
        cost = 0
        done = False
        return cost, done


class Mode:
    DIRECT = 0
    GUI = 1


def handle_data_format_for_state_diff(state_diff):
    @functools.wraps(state_diff)
    def data_format_handler(cls, state, other_state):
        if len(state.shape) == 1:
            state = state.reshape(1, -1)
        if len(other_state.shape) == 1:
            other_state = other_state.reshape(1, -1)
        diff = state_diff(cls, state, other_state)
        if type(diff) is tuple:
            if torch.is_tensor(state):
                diff = torch.cat(diff, dim=1)
            else:
                diff = np.column_stack(diff)
        return diff

    return data_format_handler


class EnvDataSource(datasource.FileDataSource):
    def __init__(self, env: Env, data_dir=None, loader_args=None, **kwargs):
        if data_dir is None:
            data_dir = self._default_data_dir()
        if loader_args is None:
            loader_args = {}
        loader_class = self._loader_map(type(env))
        if not loader_class:
            raise RuntimeError("Unrecognized data source for env {}".format(env))
        loader = loader_class(**loader_args)
        super().__init__(loader, data_dir, **kwargs)

    @staticmethod
    @abc.abstractmethod
    def _default_data_dir():
        return ""

    @staticmethod
    @abc.abstractmethod
    def _loader_map(env_type) -> typing.Union[typing.Callable, None]:
        return None

    def get_info_cols(self, info, name):
        """Get the info columns corresponding to this name"""
        assert isinstance(self.loader, TrajectoryLoader)
        return info[:, self.loader.info_desc[name]]

    def get_info_desc(self):
        """Get description of returned info columns in name: col slice format"""
        assert isinstance(self.loader, TrajectoryLoader)
        return self.loader.info_desc


def draw_ordered_end_points(vis: Visualizer, pts):
    # order_to_rgb = {(i,i+1): (1, 1, 1) for i in range(len(pts) - 1)}
    order_to_rgb = {
        (0, 1): (1, 0, 0),
        (0, 2): (0, 1, 0),
        (0, 3): (0, 0, 1),
        (3, 4): (1, 1, 1),
        (3, 5): (1, 1, 1),
        (1, 5): (1, 1, 1),
        (1, 6): (1, 1, 1),
        (6, 2): (1, 1, 1),
        (2, 4): (1, 1, 1),
        (7, 4): (1, 1, 1),
        (7, 5): (1, 1, 1),
        (7, 6): (1, 1, 1)
    }
    starts = []
    diffs = []
    rgbs = []
    for pair, rgb in order_to_rgb.items():
        f = pts[pair[0]]
        t = pts[pair[1]]
        starts.append(f)
        diffs.append(t - f)
        rgbs.append(rgb)

    vis.draw_2d_lines(f"bb", starts, diffs, rgbs, scale=1)


def draw_AABB(vis: Visualizer, aabb):
    pts = aabb_to_ordered_end_points(aabb)
    draw_ordered_end_points(vis, pts)
