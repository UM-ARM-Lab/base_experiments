import matplotlib
import pytorch_kinematics


def move_figure(f, x, y):
    """Move figure's upper left corner to pixel (x, y)"""
    backend = matplotlib.get_backend()
    if backend == 'TkAgg':
        f.canvas.manager.window.wm_geometry("+%d+%d" % (x, y))
    elif backend == 'WXAgg':
        f.canvas.manager.window.SetPosition((x, y))
    else:
        # This works for QT and GTK
        # You can also use window.setGeometry
        f.canvas.manager.window.move(x, y)


def matrix_to_pos_rot(m):
    """Convert 4x4 transformation matrix to (position, xyzw quatnerion) used by pybullet and RViz"""
    pos = m[:3, 3]
    rot = pytorch_kinematics.matrix_to_quaternion(m[:3, :3])
    rot = pytorch_kinematics.transforms.wxyz_to_xyzw(rot)
    return pos, rot
