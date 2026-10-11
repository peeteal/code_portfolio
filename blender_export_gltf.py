import os
import subprocess
import argparse
import time
import csv

def main():
    parser = argparse.ArgumentParser(description="Batch process RDC files recursively with Blender and track timing statistics.")
    parser.add_argument("--input_dir", required=True, help="Root folder containing .rdc files (recursively)")
    parser.add_argument("--output_dir", required=True, help="Root folder to store exports (mirrors input structure)")
    parser.add_argument("--csv", default="processing_stats.csv", help="CSV file to save statistics")
    parser.add_argument("--blender", default=r"D:\p4_workspace\Blender\blender.exe", help="Path to blender.exe")
    parser.add_argument("--blend_file", default="empty.blend", help="Blend file to open")
    parser.add_argument("--script", default="photogrammetry_headless_processor.py", help="Python script to run inside Blender")
    parser.add_argument("--ext", default=".rdc", help="File extension to process (default .rdc)")
    args = parser.parse_args()

    input_dir = os.path.abspath(args.input_dir)
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    csv_file = os.path.abspath(args.csv)
    csv_fields = ["file_path", "file_size_MB", "time_seconds"]
    stats = []

    total_start = time.time()

    for root, _, files in os.walk(input_dir):
        for file in files:
            if not file.lower().endswith(args.ext.lower()):
                continue

            in_path = os.path.join(root, file)
            rel_dir = os.path.relpath(root, input_dir)
            out_dir = os.path.join(output_dir, rel_dir)
            os.makedirs(out_dir, exist_ok=True)
            print(f"Creating folder {out_dir}")
            out_path = os.path.join(out_dir, os.path.splitext(file)[0])
            #print(f"Creating file at {out_path}")
            file_size_MB = os.path.getsize(in_path) / (1024*1024)

            cmd = [
                args.blender,
                "-b",
                args.blend_file,
                "--python", args.script,
                "--",
                "--in", os.path.abspath(in_path),
                "--out", os.path.abspath(out_dir)
            ]

            print(f"\n=== Processing {in_path} ({file_size_MB:.2f} MB) ===")
            start_time = time.time()
            try:
                subprocess.run(cmd, check=True)
            except subprocess.CalledProcessError as e:
                print(f"❌ Error processing {file}: {e}")
                continue
            elapsed = time.time() - start_time
            print(f"✅ Finished in {elapsed:.2f} seconds")

            stats.append({
                "file_path": os.path.abspath(in_path),
                "file_size_MB": f"{file_size_MB:.2f}",
                "time_seconds": f"{elapsed:.2f}"
            })

    total_elapsed = time.time() - total_start
    print(f"\n=== All files processed ===")
    print(f"Total time: {total_elapsed:.2f} seconds")
    if stats:
        avg_time = sum(float(s["time_seconds"]) for s in stats) / len(stats)
        print(f"Average time per file: {avg_time:.2f} seconds")

    # Save CSV
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields)
        writer.writeheader()
        writer.writerows(stats)

    print(f"Statistics saved to {csv_file}")

if __name__ == "__main__":
    main()




# nontimed version
# import os
# import subprocess
# import argparse

# def main():
#     parser = argparse.ArgumentParser(description="Batch process RDC files recursively with Blender photogrammetry script.")
#     parser.add_argument("--input_dir", required=True, help="Root folder containing .rdc files (recursively)")
#     parser.add_argument("--output_dir", required=True, help="Root folder to store exports (mirrors input structure)")
#     parser.add_argument("--blender", default=r"D:\p4_workspace\Blender\blender.exe", help="Path to blender.exe")
#     parser.add_argument("--blend_file", default="empty.blend", help="Blend file to open")
#     parser.add_argument("--script", default="photogrammetry_headless_processor.py", help="Python script to run inside Blender")
#     parser.add_argument("--ext", default=".rdc", help="File extension to process (default .rdc)")
#     args = parser.parse_args()

#     input_dir = os.path.abspath(args.input_dir)
#     output_dir = os.path.abspath(args.output_dir)
#     os.makedirs(output_dir, exist_ok=True)

#     # Walk through all subdirectories
#     for root, _, files in os.walk(input_dir):
#         for file in files:
#             if not file.lower().endswith(args.ext.lower()):
#                 continue

#             in_path = os.path.join(root, file)
#             rel_dir = os.path.relpath(root, input_dir)
#             out_dir = os.path.join(output_dir, rel_dir, os.path.splitext(file)[0])
#             os.makedirs(out_dir, exist_ok=True)

#             cmd = [
#                 args.blender,
#                 "-b",
#                 args.blend_file,
#                 "--python", args.script,
#                 "--",
#                 "--in", os.path.abspath(in_path),
#                 "--out", os.path.abspath(out_dir)
#             ]

#             print(f"\n=== Processing {in_path} ===")
#             print(" ".join(cmd))
#             try:
#                 subprocess.run(cmd, check=True)
#             except subprocess.CalledProcessError as e:
#                 print(f"❌ Error processing {file}: {e}")

# if __name__ == "__main__":
#     main()