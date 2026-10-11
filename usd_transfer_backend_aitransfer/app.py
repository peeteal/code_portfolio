import os
import sys
import argparse
#import export_lights_from_render_manifest as exporter
import utils
import exporter


def is_usd_file(path: str) -> bool:
    valid_exts = ('.usd', '.usda', '.usdc')
    try:
        return path.lower().endswith(valid_exts) and os.path.isfile(path)
    except Exception:
        return False


def read_paths_file(filepath: str):
    with open(filepath, 'r') as f:
        lines = [line.strip() for line in f if line.strip()]
    return lines


def validate_paths(paths):
    valid_paths = []
    invalid_paths = []
    for p in paths:
        if is_usd_file(p):
            valid_paths.append(p)
        else:
            invalid_paths.append(p)
    return valid_paths, invalid_paths


def get_input_path():
    while True:
        user_input = input("Enter a .usd file path or a paths file path: ").strip()
        if not user_input:
            print("Input cannot be empty. Please try again.")
            continue

        if not os.path.exists(user_input):
            print(f"Path does not exist: {user_input}")
            continue

        if is_usd_file(user_input):
            return [user_input]  # single valid USD file as a list

        # If not a USD file, treat as a paths file
        try:
            paths = read_paths_file(user_input)
        except Exception as e:
            print(f"Failed to read paths file: {e}")
            continue

        if not paths:
            print("Paths file is empty. Please provide a valid file.")
            continue

        valid_paths, invalid_paths = validate_paths(paths)
        if valid_paths:
            if invalid_paths:
                print(f"Warning: Some paths in the file are invalid or not USD files:\n  " +
                      "\n  ".join(invalid_paths))
            return valid_paths
        else:
            print("No valid .usd files found in the paths file. Please try again.")


def main(initial_path=None, export_root="./export", source_root="/upp/servers/vfxstore2/VFXstore/PROJECTS/", format=None,
         include_geometries=False, include_references=False):
    print("Running!")
    if initial_path:
        if not os.path.exists(initial_path):
            print(f"Provided initial path does not exist: {initial_path}")
            paths = get_input_path()
        elif is_usd_file(initial_path):
            paths = [initial_path]
        else:
            try:
                paths_in_file = read_paths_file(initial_path)
            except Exception as e:
                print(f"Failed to read paths file: {e}")
                paths = get_input_path()
            else:
                valid_paths, invalid_paths = validate_paths(paths_in_file)
                if valid_paths:
                    if invalid_paths:
                        print(f"Warning: Some paths in the file are invalid or not USD files:\n  " +
                              "\n  ".join(invalid_paths))
                    paths = valid_paths
                else:
                    print("No valid .usd files found in the paths file.")
                    paths = get_input_path()
    else:
        paths = get_input_path()

    print("\nProcessing the following USD file(s):")
    for input_usd_path in paths:
        print(f" - {input_usd_path}")

    # Compute base directory for relative paths
    abs_paths = [os.path.abspath(p) for p in paths]
    base_dir = os.path.commonpath(abs_paths)
    if not os.path.isdir(base_dir):
        base_dir = os.path.dirname(base_dir)

    for input_usd_path in paths:
        abs_input_path = os.path.abspath(input_usd_path)

        # Strategy:
        # - If absolute: strip leading slash and use full path under export root
        # - If relative: clean and prevent ".." from escaping the export root
        if os.path.isabs(input_usd_path):
            rel_path = os.path.relpath(abs_input_path, source_root)
            safe_rel_path = rel_path.lstrip(os.sep)
        else:
            rel_path = os.path.relpath(abs_input_path, source_root)
            rel_parts = [part for part in rel_path.split(os.sep) if part != ".."]
            safe_rel_path = os.path.join(*rel_parts)

        # Use format as extension if provided
        if format is not None:
            # ensure no leading dot
            fmt = format.lstrip('.')
            rel_path_usda = os.path.splitext(safe_rel_path)[0] + "." + fmt
        else:
            rel_path_usda = safe_rel_path

        output_usda_path = os.path.join(export_root, rel_path_usda)

        print(f"\nExtracting lights from:\n  Input: {input_usd_path}\n  Output: {output_usda_path}")
        USDExporter = exporter.Exporter()
        USDExporter.extract_filtered_from_render_manifest(
            input_usd_path,
            output_usda_path,
            source_root,
            format,
            include_geometries=include_geometries,
            include_references=include_references
        )


if __name__ == "__main__":
    print("Running main!")
    parser = argparse.ArgumentParser(description="USD light extraction app")
    parser.add_argument("path", nargs="?", help="Path to a .usd file or a file containing multiple paths")
    parser.add_argument("--format", default=None, help="Format to export as (default: None - exports as original)")
    parser.add_argument("--export-root", default="./export", help="Root folder for exports (default: ./export)")
    parser.add_argument("--source-root", default="/upp/servers/vfxstore2/VFXstore/PROJECTS/", help="Source root folder for exports (default: /upp/servers/vfxstore2/VFXstore/PROJECTS/)")
    parser.add_argument("--include-geometries", action="store_true", help="Also filter geometry prims (Mesh, BasisCurves, Points, ...)")
    parser.add_argument("--include-references", action="store_true", help="Filter prims that have authored references or payloads")
    args = parser.parse_args()

    allowed_formats = ["usd", "usdc", "usda", None]
    if args.format is not None:
        if args.format[0] == ".":
            args.format = args.format[1:]
            args.format = args.format.lower()
        if args.format.lower() not in allowed_formats:
            print(f"Error: Wrong format, only {allowed_formats} allowed.")
            exit()
        args.format = args.format.lower()

    main(args.path, args.export_root, args.source_root, args.format, args.include_geometries, args.include_references)
    exit(0)
