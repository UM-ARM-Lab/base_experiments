import abc
import logging
import pybullet as p
import random
import time
import numpy as np
import enum
import math

from datetime import datetime

import torch
from arm_pytorch_utilities import math_utils
import pytorch_kinematics as pk

import pybullet_data
from base_experiments.env.env import Visualizer, Env, Mode

logger = logging.getLogger(__name__)

state_action_color_pairs = [[(1, 0.5, 0), (1, 0.8, 0.4)],
                            [(28 / 255, 237 / 255, 143 / 255), (22 / 255, 186 / 255, 112 / 255)],
                            [(172 / 255, 17 / 255, 237 / 255), (136 / 255, 13 / 255, 189 / 256)],
                            [(181 / 255, 237 / 255, 28 / 255), (148 / 255, 194 / 255, 23 / 255)]]


def remove_user_debug_item(id):
    # p.removeUserDebugItem seems bugged and after calling it the whole simulation slows dramatically
    p.addUserDebugLine([-100, -100, -100], [-100, -100, -100], (0, 0, 0), 1, replaceItemUniqueId=id)


def make_box(half_extents, position, euler_angles, lateral_friction=0.7):
    col_id = p.createCollisionShape(p.GEOM_BOX, halfExtents=half_extents)
    vis_id = p.createVisualShape(p.GEOM_BOX, halfExtents=half_extents, rgbaColor=[0.2, 0.2, 0.2, 0.8])
    obj_id = p.createMultiBody(0, col_id, vis_id, basePosition=position,
                               baseOrientation=p.getQuaternionFromEuler(euler_angles))
    p.changeDynamics(obj_id, -1, lateralFriction=lateral_friction)
    return obj_id


def make_cylinder(radius, height, position, euler_angles, mass=1., lateral_friction=1.5, spinning_friction=0.1):
    col_id = p.createCollisionShape(p.GEOM_CYLINDER, radius=radius, height=height)
    vis_id = p.createVisualShape(p.GEOM_CYLINDER, radius=radius, length=height, rgbaColor=[0.8, 0.7, 0.3, 0.8])
    obj_id = p.createMultiBody(mass, col_id, vis_id, basePosition=position,
                               baseOrientation=p.getQuaternionFromEuler(euler_angles))
    p.changeDynamics(obj_id, -1, lateralFriction=lateral_friction, spinningFriction=spinning_friction)
    return obj_id


def make_sphere(radius, position, visual_only=False, mass=1., lateral_friction=1.5, spinning_friction=0.1,
                rgba=(0.8, 0.7, 0.3, 0.8)):
    col_id = -1
    if not visual_only:
        col_id = p.createCollisionShape(p.GEOM_SPHERE, radius=radius)
    vis_id = p.createVisualShape(p.GEOM_SPHERE, radius=radius, rgbaColor=rgba)
    obj_id = p.createMultiBody(mass, col_id, vis_id, basePosition=position)
    p.changeDynamics(obj_id, -1, lateralFriction=lateral_friction, spinningFriction=spinning_friction)
    return obj_id


_CONTACT_TESTER_ID = -1


def closest_point_on_surface(object_id, query_point, return_full_contact_info=True):
    # create query object if it doesn't exist
    global _CONTACT_TESTER_ID
    if _CONTACT_TESTER_ID == -1:
        col_id = p.createCollisionShape(p.GEOM_SPHERE, radius=1e-8)
        vis_id = p.createVisualShape(p.GEOM_SPHERE, radius=0.003, rgbaColor=[0.1, 0.9, 0.3, 0.6])
        _CONTACT_TESTER_ID = p.createMultiBody(0, col_id, vis_id, basePosition=query_point)

    p.resetBasePositionAndOrientation(_CONTACT_TESTER_ID, query_point, [0, 0, 0, 1])
    p.performCollisionDetection()
    pts_on_surface = p.getClosestPoints(object_id, _CONTACT_TESTER_ID, 100, linkIndexB=-1)
    # if the pybullet environment is reset and the object doesn't exist; this will not catch all cases
    if len(pts_on_surface) < 1:
        _CONTACT_TESTER_ID = -1
        return closest_point_on_surface(object_id, query_point, return_full_contact_info=return_full_contact_info)

    pts_on_surface = sorted(pts_on_surface, key=lambda c: c[ContactInfo.DISTANCE])

    # move out the way
    p.resetBasePositionAndOrientation(_CONTACT_TESTER_ID, [0, 0, 100], [0, 0, 0, 1])
    ret = pts_on_surface[0]
    if not return_full_contact_info:
        ret = ret[ContactInfo.POS_A]
    return ret


def surface_normal_at_point(object_id, query_point):
    # find contact normal on point of object closest to query point
    query_point = closest_point_on_surface(object_id, query_point, return_full_contact_info=False)

    global _CONTACT_TESTER_ID
    if _CONTACT_TESTER_ID == -1:
        col_id = p.createCollisionShape(p.GEOM_SPHERE, radius=1e-8)
        vis_id = p.createVisualShape(p.GEOM_SPHERE, radius=0.003, rgbaColor=[0.1, 0.9, 0.3, 0.6])
        _CONTACT_TESTER_ID = p.createMultiBody(0, col_id, vis_id, basePosition=query_point)

    p.resetBasePositionAndOrientation(_CONTACT_TESTER_ID, query_point, [0, 0, 0, 1])
    p.performCollisionDetection()
    pts_on_surface = p.getContactPoints(object_id, _CONTACT_TESTER_ID, linkIndexB=-1)
    # if the pybullet environment is reset and the object doesn't exist; this will not catch all cases
    if len(pts_on_surface) < 1:
        return np.zeros(3)

    # move out the way
    p.resetBasePositionAndOrientation(_CONTACT_TESTER_ID, [0, 0, 100], [0, 0, 0, 1])
    return pts_on_surface[0][ContactInfo.NORMAL_DIR_B]


class PybulletEnv(Env):
    LINK_FRAME_POS = [0, 0, 0]
    LINK_FRAME_ORIENTATION = [0, 0, 0, 1]

    def __init__(self, mode=Mode.DIRECT, log_video=False, video_name="", default_debug_height=0, camera_dist=1.5,
                 background=None):
        self.log_video = log_video
        self.video_name = video_name
        self.mode = mode
        self.realtime = False
        self.sim_step_s = 1. / 240.
        self.randseed = None
        self.background = background

        # quadratic cost
        self.Q = self.state_cost()
        self.R = self.control_cost()

        self._configure_physics_engine()
        self._dd = DebugDrawer(default_debug_height, camera_dist)

    def set_camera_position(self, camera_pos, yaw=0, pitch=-89):
        self._dd.set_camera_position(camera_pos, yaw, pitch)

    def _configure_physics_engine(self):
        mode_dict = {Mode.GUI: p.GUI, Mode.DIRECT: p.DIRECT}

        # if the mode we gave is in the dict then use it, otherwise use the given mode value as is
        mode = mode_dict.get(self.mode) or self.mode

        options = ''
        if self.background is not None:
            brgb = self.background
            options = f'--background_color_red={brgb[0]} --background_color_green={brgb[1]} --background_color_blue={brgb[2]}'
        self.physics_client = p.connect(mode, options=options)  # p.GUI for GUI or p.DIRECT for non-graphical version

        # disable useless menus on the left and right
        p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
        if self.log_video:
            if self.video_name == "":
                self.video_name = datetime.now().strftime('%Y_%m_%d_%H_%M_%S')
            self.logging_id = p.startStateLogging(p.STATE_LOGGING_VIDEO_MP4, "{}.mp4".format(self.video_name))

        # use data provided by PyBullet
        p.setAdditionalSearchPath(pybullet_data.getDataPath())  # optionally

        if self.realtime:
            p.setRealTimeSimulation(True)
        else:
            p.setRealTimeSimulation(False)
            p.setTimeStep(self.sim_step_s)

    def seed(self, randseed=None):
        random.seed(time.time())
        if randseed is None:
            randseed = random.randint(0, 1000000)
        logger.info('random seed: %d', randseed)
        self.randseed = randseed
        random.seed(randseed)
        # potentially also randomize the starting configuration

    def close(self):
        if self.log_video:
            p.stopStateLogging(self.logging_id)
        p.disconnect(self.physics_client)

    def draw_user_text(self, text, location_index=1, left_offset=1.0, xy=None):
        if xy:
            self._dd.draw_screen_text('user_{}'.format(xy), text, xy)
        else:
            if location_index == 0:
                raise RuntimeError("Can't use same location index (0) as cost")
            self._dd.draw_text('user{}_{}'.format(location_index, left_offset), text, location_index, left_offset)

    @property
    def vis(self):
        return self._dd

    @property
    @abc.abstractmethod
    def robot_id(self):
        """Return the unique pybullet ID of the robot"""

    @abc.abstractmethod
    def _draw_action(self, action, old_state=None, debug=0):
        pass

    def visualize_state_actions(self, base_name, states, actions, state_c, action_c, action_scale):
        pass

    @abc.abstractmethod
    def visualize_goal_set(self, states):
        pass

    @abc.abstractmethod
    def visualize_trap_set(self, states):
        pass

    @abc.abstractmethod
    def visualize_contact_set(self, contact_set):
        pass

    @abc.abstractmethod
    def visualize_rollouts(self, states):
        pass

    @abc.abstractmethod
    def visualize_prediction_error(self, predicted_state):
        """In GUI mode, show the difference between the predicted state and the current actual state"""

    @staticmethod
    def _make_robot_translucent(robot_id, alpha=0.4):
        def make_transparent(link):
            link_id = link[1]
            rgba = list(link[7])
            rgba[3] = alpha
            p.changeVisualShape(robot_id, link_id, rgbaColor=rgba)

        visual_data = p.getVisualShapeData(robot_id)
        for link in visual_data:
            make_transparent(link)

    @staticmethod
    def get_com_tf(robot_id):
        info = p.getDynamicsInfo(robot_id, -1)
        pos_com = torch.tensor(info[3])
        xyzw_com = torch.tensor(info[4])
        return pk.Transform3d(pos=pos_com, rot=pk.xyzw_to_wxyz(xyzw_com))

    @staticmethod
    def reset_base_link_frame(robot_id, pos, rpy):
        """pybullet uses the center of mass for get/reset base position and orientation, this provides an alternative
        to specifying the base link frame."""
        com_tf = PybulletEnv.get_com_tf(robot_id)
        baseOrientation = p.getQuaternionFromEuler(rpy)
        # convert to COM frame
        world_to_base_tf = pk.Transform3d(pos=torch.tensor(pos),
                                          rot=pk.xyzw_to_wxyz(torch.tensor(baseOrientation)))
        world_to_base_com_tf = world_to_base_tf.compose(com_tf)
        pos, xyzw = pk.rotation_conversions.matrix_to_pos_rot(world_to_base_com_tf.get_matrix())
        p.resetBasePositionAndOrientation(robot_id, pos[0], xyzw[0])

    @staticmethod
    def get_base_link_frame(robot_id):
        """pybullet uses the center of mass for get/reset base position and orientation, this provides an alternative
        for retrieving the base link frame."""
        com_tf = PybulletEnv.get_com_tf(robot_id)
        pos, xyzw = p.getBasePositionAndOrientation(robot_id)
        world_to_base_com_tf = pk.Transform3d(pos=torch.tensor(pos),
                                              rot=pk.xyzw_to_wxyz(torch.tensor(xyzw)))
        world_to_base_tf = world_to_base_com_tf.compose(com_tf.inverse())
        return world_to_base_tf


class ContactInfo(enum.IntEnum):
    """Semantics for indices of a contact info from getContactPoints"""
    LINK_A = 3
    LINK_B = 4
    POS_A = 5
    POS_B = 6
    NORMAL_DIR_B = 7
    DISTANCE = 8
    NORMAL_MAG = 9
    LATERAL1_MAG = 10
    LATERAL1_DIR = 11
    LATERAL2_MAG = 12
    LATERAL2_DIR = 13


def get_total_contact_force(contact, flip=True):
    force_sign = -1 if flip else 1
    force = force_sign * contact[ContactInfo.NORMAL_MAG]
    dv = [force * v for v in contact[ContactInfo.NORMAL_DIR_B]]
    fyd, fxd = get_lateral_friction_forces(contact, flip)
    f_all = [sum(i) for i in zip(dv, fyd, fxd)]
    return f_all


def get_lateral_friction_forces(contact, flip=True):
    force_sign = -1 if flip else 1
    fy = force_sign * contact[ContactInfo.LATERAL1_MAG]
    fyd = [fy * v for v in contact[ContactInfo.LATERAL1_DIR]]
    fx = force_sign * contact[ContactInfo.LATERAL2_MAG]
    fxd = [fx * v for v in contact[ContactInfo.LATERAL2_DIR]]
    return fyd, fxd


class DebugDrawer(Visualizer):
    def __init__(self, default_height, camera_height):
        self._debug_ids = {}
        self._mesh_to_single_id = {}
        self._drawn_mesh_ids = set()
        self._camera_pos = None
        self._camera_height = camera_height
        self._default_height = default_height
        self._3dmode = False
        self._inv_camera_tsf = None
        self._mesh_shape_ids = {}
        self._hide_text = False
        self.set_camera_position([0, 0])

    def set_hide_text(self, hide_text):
        self._hide_text = hide_text

    def set_camera_position(self, camera_pos, yaw=0, pitch=-89):
        self._camera_pos = camera_pos
        for _ in range(1000):
            p.stepSimulation()
        z = 0
        if len(self._camera_pos) > 2:
            z = self._camera_pos[2]
        p.resetDebugVisualizerCamera(cameraDistance=self._camera_height, cameraYaw=yaw, cameraPitch=pitch,
                                     cameraTargetPosition=[camera_pos[0], camera_pos[1], z])
        # wait for reset
        for _ in range(1000):
            p.stepSimulation()
        # cache the inverse camera transform for efficiency
        info = p.getDebugVisualizerCamera()
        if info[0] == 0 and info[1] == 0:
            logger.debug("Setting empty camera; check that we are not in GUI mode")
        else:
            view_matrix = np.array(info[2]).reshape(4, 4).T
            self._inv_camera_tsf = np.linalg.inv(view_matrix)

    def toggle_3d(self, using_3d):
        self._3dmode = using_3d

    def _process_point_height(self, point, height):
        if height is None:
            if self._3dmode:
                height = point[2]
            else:
                height = self._default_height
        return height

    def draw_point(self, name, point, color=(0, 0, 0), length=0.01, length_ratio=1, rot=0, height=None, label=None,
                   scale=2):
        if name not in self._debug_ids:
            self._debug_ids[name] = [-1, -1, -1]
        uids = self._debug_ids[name]
        l = length

        if torch.is_tensor(point):
            point = point.detach().cpu()
        # ignore 3rd dimension if it exists to plot everything at the same height
        height = self._process_point_height(point, height)

        location = (point[0], point[1], height)
        c = math.cos(rot)
        s = math.sin(rot)
        uids[0] = p.addUserDebugLine(np.add(location, [l * c, l * s, 0]),
                                     np.add(location, [-l * c, - l * s, 0]), color, scale,
                                     replaceItemUniqueId=uids[0])
        uids[1] = p.addUserDebugLine(np.add(location, [- l * s * length_ratio, l * length_ratio * c, 0]),
                                     np.add(location, [l * s * length_ratio, -l * length_ratio * c, 0]), color,
                                     scale,
                                     replaceItemUniqueId=uids[1])
        if label is not None:
            uids[2] = p.addUserDebugText(label,
                                         [location[0], location[1], location[2]],
                                         textColorRGB=color,
                                         textSize=2,
                                         replaceItemUniqueId=uids[2])
        return uids

    def draw_2d_pose(self, name, pose, color=(0, 0, 0), length=0.15 / 2, height=None):
        height = self._process_point_height(pose, height)
        if name not in self._debug_ids:
            self._debug_ids[name] = [-1, -1]
        uids = self._debug_ids[name]

        location = (pose[0], pose[1], height)
        side_lines = math_utils.rotate_wrt_origin((0, length * 0.2), pose[2])
        pointer = math_utils.rotate_wrt_origin((length, 0), pose[2])
        uids[0] = p.addUserDebugLine(np.add(location, [side_lines[0], side_lines[1], 0]),
                                     np.add(location, [-side_lines[0], -side_lines[1], 0]),
                                     color, 2, replaceItemUniqueId=uids[0])
        uids[1] = p.addUserDebugLine(np.add(location, [0, 0, 0]),
                                     np.add(location, [pointer[0], pointer[1], 0]),
                                     color, 2, replaceItemUniqueId=uids[1])
        return uids

    def clear_visualizations(self, names=None):
        if names is None:
            p.removeAllUserDebugItems()
            self._debug_ids = {}
            for mesh in self._drawn_mesh_ids:
                p.removeBody(mesh)
            self._drawn_mesh_ids = set()
            self._mesh_to_single_id = {}
            return

        for name in names:
            if name not in self._debug_ids:
                continue
            uids = self._debug_ids.pop(name)
            if type(uids) is int:
                uids = [uids]
            for id in uids:
                remove_user_debug_item(id)

    def clear_visualization_after(self, prefix, index):
        name = "{}.{}".format(prefix, index)
        while name in self._debug_ids:
            uids = self._debug_ids.pop(name)
            if type(uids) is int:
                uids = [uids]
            for id in uids:
                remove_user_debug_item(id)
            index += 1
            name = "{}.{}".format(prefix, index)

    def draw_2d_line(self, name, start, diff, color=(0, 0, 0), size=2., scale=0.4):
        if name not in self._debug_ids:
            self._debug_ids[name] = -1
        uid = self._debug_ids[name]
        if torch.is_tensor(start):
            start = start.detach().cpu()
        if torch.is_tensor(diff):
            diff = diff.detach().cpu()

        self._debug_ids[name] = p.addUserDebugLine(start, np.add(start, [diff[0] * scale, diff[1] * scale,
                                                                         diff[2] * scale if len(diff) == 3 else 0]),
                                                   color, lineWidth=size, replaceItemUniqueId=uid)
        return self._debug_ids[name]

    def draw_contact_point(self, name, contact, flip=True):
        start = contact[ContactInfo.POS_A]
        f_all = get_total_contact_force(contact, flip)
        # combined normal vector (adding lateral friction)
        f_size = np.linalg.norm(f_all)
        self.draw_2d_line("{} xy".format(name), start, f_all, size=f_size, scale=0.03, color=(1, 1, 0))
        # _draw_contact_friction(line_unique_ids, contact, flip)
        return f_size

    def draw_contact_friction(self, name, contact, flip=True, height=None):
        start = list(contact[ContactInfo.POS_A])
        start[2] = self._process_point_height(start, height)
        # friction along y
        scale = 0.1
        c = (1, 0.4, 0.7)
        fyd, fxd = get_lateral_friction_forces(contact, flip)
        uidsx = self.draw_2d_line('{}y'.format(name), start, fyd, size=np.linalg.norm(fyd), scale=scale, color=c)
        uidsy = self.draw_2d_line('{}x'.format(name), start, fxd, size=np.linalg.norm(fxd), scale=scale, color=c)
        return uidsx + uidsy

    def draw_transition(self, prev_block, new_block, height=None):
        name = 't'
        if name not in self._debug_ids:
            self._debug_ids[name] = []

        self._debug_ids[name].append(
            p.addUserDebugLine([prev_block[0], prev_block[1], self._process_point_height(prev_block, height)],
                               (new_block[0], new_block[1], self._process_point_height(new_block, height)),
                               [0, 0, 1], 2))

    def clear_transitions(self):
        name = 't'
        if name in self._debug_ids:
            for line in self._debug_ids[name]:
                remove_user_debug_item(line)
            self._debug_ids[name] = []

    def draw_text(self, name, text, location_index, left_offset=1., offset_in_z=False):
        if self._hide_text:
            return
        if name not in self._debug_ids:
            self._debug_ids[name] = -1
        uid = self._debug_ids[name]

        z = 0.1
        move_down = location_index * 0.15
        if offset_in_z:
            move_down = 0
            z += location_index * 0.1

        height_scale = self._camera_height * 0.7
        self._debug_ids[name] = p.addUserDebugText(str(text),
                                                   [self._camera_pos[0] + left_offset * height_scale,
                                                    self._camera_pos[1] + (1 - move_down) * height_scale, z],
                                                   textColorRGB=[0.5, 0.1, 0.1],
                                                   textSize=2,
                                                   replaceItemUniqueId=uid)
        return self._debug_ids[name]

    def draw_screen_text(self, name, text, camera_frame_pos):
        if self._hide_text:
            return
        # not in camera mode, ignore
        if self._inv_camera_tsf is None:
            return
        if name not in self._debug_ids:
            self._debug_ids[name] = -1
        uid = self._debug_ids[name]

        # convert from camera frame to world frame
        pos_in = np.r_[camera_frame_pos, 1]
        world_frame_pos = self._inv_camera_tsf @ pos_in

        self._debug_ids[name] = p.addUserDebugText(str(text),
                                                   world_frame_pos[:3],
                                                   textColorRGB=[0.5, 0.1, 0.1],
                                                   textSize=2,
                                                   replaceItemUniqueId=uid)
        return self._debug_ids[name]

    def draw_mesh(self, name, model, pose, rgba=(0, 0, 0, 1.), scale=1., object_id=None, vis_frame_pos=(0, 0, 0),
                  vis_frame_rot=(0, 0, 0, 1)):
        visual_shape_name = f"{model}"
        visual_shape_id = self._mesh_shape_ids.get(visual_shape_name, None)
        if visual_shape_id is None:
            visual_shape_id = p.createVisualShape(shapeType=p.GEOM_MESH,
                                                  fileName=model,
                                                  rgbaColor=rgba, meshScale=[scale, scale, scale],
                                                  visualFrameOrientation=vis_frame_rot,
                                                  visualFramePosition=vis_frame_pos)
            self._mesh_shape_ids[visual_shape_name] = visual_shape_id
        pos, rot = pose

        if object_id == self.USE_DEFAULT_ID_FOR_NAME:
            object_id = self._mesh_to_single_id.get(name, None)

        if object_id is None or object_id not in self._drawn_mesh_ids:
            object_id = p.createMultiBody(baseMass=0, basePosition=pos, baseVisualShapeIndex=visual_shape_id)
        p.resetBasePositionAndOrientation(object_id, pos, rot)
        p.changeVisualShape(object_id, -1, rgbaColor=rgba)
        self._drawn_mesh_ids.add(object_id)
        self._mesh_to_single_id[name] = object_id

        return object_id


def pybullet_obj_range(obj_id, padding=0):
    aabb = p.getAABB(obj_id)
    world_min, world_max = aabb
    # already scaled, but we add a little padding
    ranges = np.array(list(zip(world_min, world_max)))
    ranges[:, 0] -= padding
    ranges[:, 1] += padding
    return ranges
