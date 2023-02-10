import os

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
DATA_DIR = os.path.expanduser('~/experiments/data')
VIDEO_DIR = os.path.expanduser('~/experiments/video')
URDF_DIR = os.path.expanduser('~/experiments/urdf')

PIC_SAVE_DIR = os.path.expanduser('~/experiments/pics')

ros_pkg_name = None


def ensure_rviz_resource_path(filepath):
    """Sanitize some path to something in this package to be RVIZ resource loader compatible"""
    # get path after the first instance
    if ros_pkg_name is None:
        raise RuntimeError("ros_pkg_name must be set with base_experiments.fg.ros_pkg_name = 'name' "
                           "to allow finding of resources")
    relative_path = filepath.partition(ros_pkg_name)[2]
    return f"package://{ros_pkg_name}/{relative_path.strip('/')}"
