"""
Needs to be run as administrator!
Use install.bat: Right click -> "Run as administrator"
"""

import ctypes
import os
from pathlib import Path
import shutil
import stat

from typing import TypeVar


class TerminalColours:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


def print_fail(msg: str):
    print(msg, end="\n\n")
    # print(TerminalColours.FAIL + msg + TerminalColours.ENDC)


def print_warning(msg: str):
    print(msg, end="\n\n")
    # print(TerminalColours.WARNING + msg + TerminalColours.ENDC)


def print_ok(msg: str):
    print(msg, end="\n\n")
    # print(TerminalColours.OKBLUE + msg + TerminalColours.ENDC)


T = TypeVar("T")


def select_option(options: list[T], *, none_msg: str, one_msg: str, many_msg: str) -> T:
    if len(options) < 1:
        print_fail(none_msg)
        exit(0)
    elif len(options) == 1:
        print_ok(one_msg.format(single_option=options[0]))
        return options[0]
    else:
        while True:
            print_ok(many_msg.format(num_options=len(options)))
            for i, option in enumerate(options):
                print(f"({i+1}) {str(option)}")
            selection = input("Selection: ")
            try:
                selection = int(selection) - 1
                if selection < 0:
                    raise ValueError()
                return options[selection]
            except (ValueError, IndexError):
                print_fail("Unknown selection!\n")


def find_repo_config_json(repo_path: Path) -> Path:
    config_json = repo_path / "config.json"
    if not config_json.exists():
        print_fail(f"ERROR: expecting to find config.json at {config_json}")
        exit(0)

    return config_json


def find_codesys_install_paths() -> list[Path]:
    program_files = Path("C:/Program Files")
    program_files_x86 = Path("C:/Program Files (x86)")

    found_paths = []

    for prg_files_path in [program_files, program_files_x86]:
        for child in prg_files_path.iterdir():
            if child.is_dir() and "CODESYS" in child.name:
                found_paths.append(child)

    return found_paths


def get_or_create_script_path(codesys_install_path: Path) -> Path:
    codesys_path = codesys_install_path / "CODESYS"
    if not codesys_path.exists() or not codesys_path.is_dir():
        print_fail(f"ERROR: expected directory to exist: {codesys_path}")
        exit(0)

    script_path = codesys_path / "Script Commands"
    if not script_path.exists():
        print_ok(f"Creating directory: {script_path}")
        script_path.mkdir()

    if not script_path.is_dir():
        print_fail(f"ERROR: expected to be a directory: {script_path}")
        exit(0)

    return script_path


def rename_or_get_config_json_destination(script_path: Path) -> Path:
    config_json = script_path / "config.json"
    if config_json.exists():
        print_warning(f"WARNING: existing config found")
        success = False
        for i in range(100):
            try:
                backup_json = f"config.backup_{i}.json"
                backup_path = config_json.parent / backup_json
                config_json.rename(backup_path)
                print_ok(f"Renamed config.json to {backup_json}")
                success = True
                break
            except FileExistsError:
                pass

        if not success:
            print_fail(f"ERROR: file already exists: {backup_path}.")
            print_fail(f"Please remove some backups in {backup_path.parent}")
            exit(0)

    return config_json


def copy_config_json(repo_config: Path, config_destination: Path):
    try:
        shutil.copy(repo_config, config_destination)
    except Exception as e:
        print_fail(f"ERROR copying config json: {e}")
        exit(0)

    print_ok(f"SUCCESS: config written to {config_destination}")


def remove_existing_symlink(symlink_folder: Path):
    # exists() and is_symlink() both report False for a junction whose target
    # is gone, yet the reparse point still blocks CreateSymbolicLinkW with
    # ERROR_ALREADY_EXISTS, so probe the path itself with lstat
    try:
        attributes = os.lstat(symlink_folder).st_file_attributes
    except FileNotFoundError:
        return

    if symlink_folder.is_symlink():
        old_target = os.readlink(symlink_folder)
        try:
            # directory symlinks on Windows must be removed with rmdir, not unlink
            symlink_folder.rmdir()
        except OSError as e:
            print_fail(f"ERROR removing existing symlink {symlink_folder}: {e}")
            exit(0)

        print_ok(f"Removed existing symlink to {old_target}")
    elif attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        print_fail(f"ERROR: {symlink_folder} is a junction or other reparse point, not a symlink.")
        print_fail(f"Please remove it manually (rmdir), then run this installer again.")
        exit(0)
    else:
        print_fail(f"ERROR: {symlink_folder} exists and is not a symlink.")
        print_fail(f"Please remove it manually, then run this installer again.")
        exit(0)


def symlink_install_repo_folder(repo: Path, destination: Path):
    symlink_folder = destination / "codescribe"
    remove_existing_symlink(symlink_folder)

    kdll = ctypes.WinDLL("kernel32", use_last_error=True)
    create_symbolic_link = kdll.CreateSymbolicLinkW
    create_symbolic_link.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    create_symbolic_link.restype = ctypes.c_ubyte  # BOOLEAN, a single byte

    flag_is_a_directory = 1
    created = create_symbolic_link(str(symlink_folder), str(repo), flag_is_a_directory)

    if not created:
        error_code = ctypes.get_last_error()
        print_fail(f"ERROR creating symlink {symlink_folder} -> {repo}")
        error_privilege_not_held = 1314
        if error_code == error_privilege_not_held:
            print_fail(f"Permission error! Are you running as administrator?")
        else:
            print_fail(f"CreateSymbolicLinkW failed with error code {error_code}")
        exit(0)

    print_ok(f"SUCCESS: symlink created {symlink_folder} -> {repo}")


if __name__ == "__main__":
    try:
        print()
        repo_path = Path(__file__).parent
        repo_config_json = find_repo_config_json(repo_path)

        install_paths = find_codesys_install_paths()
        install_path = select_option(
            install_paths,
            none_msg="No CODESYS install paths found!",
            one_msg="CODESYS install path found: {single_option}",
            many_msg="{num_options} CODESYS install paths found:",
        )

        script_path = get_or_create_script_path(install_path)

        # symlink first: if it fails we exit without having backed away the
        # existing config.json, leaving the previous install intact
        symlink_install_repo_folder(repo_path, script_path)

        config_json_destination = rename_or_get_config_json_destination(script_path)
        copy_config_json(repo_config_json, config_json_destination)
    except PermissionError:
        print_fail(f"Permission error! Are you running as administrator?")
        exit(0)
