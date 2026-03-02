#!/usr/bin/env python3
"""
MLC Max Bandwidth Characterization Script

Runs intel MLC --max_bandwidth with a full sweep of buffer sizes and core counts,
targeting a specific NUMA node via numactl.

Output: Timestamped result folder containing individual run outputs and a summary CSV.

Prerequisites:
    conda activate mlc_3p12
"""

import argparse
import shutil
import subprocess
import csv
import re
import os
import sys
from datetime import datetime
from itertools import product
from tqdm import tqdm


DEFAULT_TIMEOUT = 600


# Default buffer sizes to sweep
DEFAULT_BUFFER_SIZES = ["64m", "128m", "256m", "512m", "1g", "2g", "4g", "8g", "16g", "32g"]

# Default core counts (powers of 2): generates k1-1, k1-2, k1-4, ..., k1-32
DEFAULT_CORE_COUNTS = [1, 2, 4, 8, 16, 32]

# R/W patterns MLC reports in max_bandwidth output
RW_PATTERNS = [
    "ALL Reads",
    "3:1 Reads-Writes",
    "2:1 Reads-Writes",
    "1:1 Reads-Writes",
    "Stream-triad like",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="MLC max_bandwidth sweep across buffer sizes and core counts"
    )
    parser.add_argument("--mlc-path", required=True, help="Path to MLC binary")
    parser.add_argument("--device-id", required=True, type=int, help="NUMA node ID for numactl -m")
    parser.add_argument(
        "--buffer-sizes", nargs="+", default=DEFAULT_BUFFER_SIZES,
        help=f"Buffer sizes to sweep (default: {' '.join(DEFAULT_BUFFER_SIZES)})"
    )
    parser.add_argument(
        "--core-counts", nargs="+", type=int, default=DEFAULT_CORE_COUNTS,
        help=f"Core counts to sweep (default: {' '.join(map(str, DEFAULT_CORE_COUNTS))})"
    )
    parser.add_argument("--start-core", type=int, default=1, help="Starting core ID (default: 1)")
    parser.add_argument("--iterations", type=int, default=1, help="Iterations per config (default: 1)")
    parser.add_argument("--output-dir", default=".", help="Parent directory for results (default: current dir)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"Timeout in seconds per MLC run (default: {DEFAULT_TIMEOUT})")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running")
    return parser.parse_args()


def create_result_folder(parent_dir, device_id):
    """Create result folder: results/<date>/max_bw/node<id>/"""
    date_str = datetime.now().strftime("%Y%m%d")
    folder_path = os.path.join(parent_dir, "results", date_str, "max_bw", f"node{device_id}")
    os.makedirs(folder_path, exist_ok=True)
    return folder_path


def build_command(mlc_path, device_id, start_core, core_count, buffer_size):
    end_core = start_core + core_count - 1
    core_range = f"{start_core}-{end_core}"
    cmd = [
        "sudo", "numactl", "-m", str(device_id), mlc_path,
        "--max_bandwidth", f"-k{core_range}", f"-b{buffer_size}",
    ]
    return cmd, core_range


def parse_mlc_output(output):
    """Parse MLC max_bandwidth stdout and return dict of pattern -> bandwidth in MB/s."""
    results = {}
    for pattern in RW_PATTERNS:
        regex = re.escape(pattern) + r"\s*:\s*([\d.]+)"
        match = re.search(regex, output)
        if match:
            results[pattern] = float(match.group(1))
        else:
            results[pattern] = None
    return results


def run_single(cmd, timeout):
    """Run a single MLC command and return stdout and stderr."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode != 0:
            tqdm.write(f"  WARNING: non-zero return code {result.returncode}")
            if result.stderr:
                tqdm.write(f"  stderr: {result.stderr.strip()}")
        return result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        tqdm.write(f"  ERROR: command timed out ({timeout}s)")
        return None, None
    except Exception as e:
        tqdm.write(f"  ERROR: {e}")
        return None, None


def save_run_file(folder_path, run_num, cmd, buffer_size, core_range, iteration, stdout, stderr):
    """Save individual run output to a file."""
    filename = f"run_{run_num:03d}_b{buffer_size}_k{core_range}_iter{iteration}.txt"
    filepath = os.path.join(folder_path, filename)
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
    with open(filepath, "w") as f:
        f.write(f"Command:\n{cmd_str}\n")
        f.write(f"{'-'*60}\n")
        f.write(f"Output result:\n")
        f.write(stdout or "(empty)")
        if stderr and stderr.strip():
            f.write(f"\n{'-'*60}\n")
            f.write(f"Errors:\n")
            f.write(stderr)
    return filepath


def main():
    args = parse_args()

    if not args.dry_run:
        mlc_resolved = shutil.which(args.mlc_path)
        if mlc_resolved is None and not os.path.isfile(args.mlc_path):
            print(f"ERROR: MLC binary not found at {args.mlc_path}")
            sys.exit(1)
        if mlc_resolved:
            args.mlc_path = mlc_resolved

        # Check sudo is available without a password prompt
        ret = subprocess.run(["sudo", "-n", "true"], capture_output=True)
        if ret.returncode != 0:
            print("ERROR: sudo access required but 'sudo -n true' failed.")
            print("       Please ensure passwordless sudo is configured or run 'sudo -v' first.")
            sys.exit(1)

        # Validate core ranges
        max_core = args.start_core + max(args.core_counts) - 1
        n_cpus = os.cpu_count()
        if n_cpus is not None and max_core >= n_cpus:
            print(f"ERROR: Core range extends to core {max_core}, but system only has {n_cpus} cores (0-{n_cpus - 1}).")
            print(f"       Adjust --start-core or --core-counts.")
            sys.exit(1)

    configs = list(product(args.buffer_sizes, args.core_counts))
    total_runs = len(configs) * args.iterations

    print(f"MLC Max Bandwidth Sweep")
    print(f"  NUMA node:     {args.device_id}")
    print(f"  MLC path:      {args.mlc_path}")
    print(f"  Buffer sizes:  {args.buffer_sizes}")
    print(f"  Core counts:   {args.core_counts}")
    print(f"  Start core:    {args.start_core}")
    print(f"  Iterations:    {args.iterations}")
    print(f"  Total runs:    {total_runs}")
    print()

    if args.dry_run:
        print("DRY RUN — commands that would be executed:")
        for buf, cores in configs:
            cmd, core_range = build_command(args.mlc_path, args.device_id, args.start_core, cores, buf)
            print(f"  {' '.join(cmd)}")
        return

    result_folder = create_result_folder(args.output_dir, args.device_id)
    summary_csv = os.path.join(result_folder, "summary.csv")
    print(f"  Results folder: {result_folder}")
    print()

    fieldnames = [
        "run_num", "device_id", "buffer_size", "core_range", "num_cores", "iteration",
        "all_reads_MBs", "3to1_rw_MBs", "2to1_rw_MBs", "1to1_rw_MBs", "stream_triad_MBs",
        "run_file",
    ]

    with open(summary_csv, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        run_num = 0
        all_runs = [
            (buf, cores, iteration)
            for buf, cores in configs
            for iteration in range(1, args.iterations + 1)
        ]
        pbar = tqdm(total=len(all_runs), desc="MLC sweep", unit="run")
        try:
            for buf, cores, iteration in all_runs:
                run_num += 1
                cmd, core_range = build_command(
                    args.mlc_path, args.device_id, args.start_core, cores, buf
                )

                tqdm.write(f"\n[{run_num}/{len(all_runs)}] buffer={buf}, cores={cores}, iter={iteration}")
                tqdm.write(f"  Command: {' '.join(cmd)}")

                stdout, stderr = run_single(cmd, args.timeout)

                if stdout:
                    tqdm.write(stdout.strip())

                run_file = save_run_file(
                    result_folder, run_num, cmd, buf, core_range, iteration,
                    stdout, stderr
                )

                pbar.update(1)

                if stdout is None:
                    continue

                parsed = parse_mlc_output(stdout)

                row = {
                    "run_num": run_num,
                    "device_id": args.device_id,
                    "buffer_size": buf,
                    "core_range": core_range,
                    "num_cores": cores,
                    "iteration": iteration,
                    "all_reads_MBs": parsed.get("ALL Reads"),
                    "3to1_rw_MBs": parsed.get("3:1 Reads-Writes"),
                    "2to1_rw_MBs": parsed.get("2:1 Reads-Writes"),
                    "1to1_rw_MBs": parsed.get("1:1 Reads-Writes"),
                    "stream_triad_MBs": parsed.get("Stream-triad like"),
                    "run_file": os.path.basename(run_file),
                }
                writer.writerow(row)
                csvfile.flush()
                os.fsync(csvfile.fileno())
        except KeyboardInterrupt:
            print(f"\n\nInterrupted after {run_num} runs. Partial results saved.")
        finally:
            pbar.close()

    print(f"\nDone. Results saved to {result_folder}/")
    print(f"  Summary: {summary_csv}")
    print(f"  Individual runs: {run_num} files")


if __name__ == "__main__":
    main()