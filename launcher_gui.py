import sys

from swp2tex.gui import launch_gui


if __name__ == "__main__":
    initial_main = sys.argv[1] if len(sys.argv) > 1 else None
    launch_gui(initial_main=initial_main)
