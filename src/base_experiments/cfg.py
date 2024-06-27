import os

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
DATA_DIR = os.path.expanduser('~/experiments/data')
VIDEO_DIR = os.path.expanduser('~/experiments/video')
MESH_DIR = os.path.join(ROOT_DIR, "meshes")
URDF_DIR = os.path.join(ROOT_DIR, "urdf")
LOG_DIR = os.path.expanduser('~/experiments/logs')

PIC_SAVE_DIR = os.path.expanduser('~/experiments/pics')

ros_pkg_name = "base_experiments"


def ensure_rviz_resource_path(filepath):
    """Sanitize some path to something in this package to be RVIZ resource loader compatible"""
    # get path after the first instance
    if ros_pkg_name is None:
        raise RuntimeError("ros_pkg_name must be set with base_experiments.fg.ros_pkg_name = 'name' "
                           "to allow finding of resources")
    if ros_pkg_name not in filepath:
        raise ValueError(f"Path {filepath} does not contain package name {ros_pkg_name} (objects need to be in "
                         f"package for rviz to visualize)")
    relative_path = filepath.partition(ros_pkg_name)[2]
    return f"package://{ros_pkg_name}/{relative_path.strip('/')}"
