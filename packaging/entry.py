"""Einstiegspunkt für PyInstaller."""
import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()   # muss vor allem anderen laufen (Arbeitsprozesse der EXE)
    from nrmm.app import main
    sys.exit(main())
