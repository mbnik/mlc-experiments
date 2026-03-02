#!/usr/bin/env python3
"""
MLC Idle Latency Characterization Script

Runs Intel MLC --idle_latency with a sweep of buffer sizes on a single core,
targeting a specific NUMA node via numactl.

Output: Results folder containing individual run outputs and a summary CSV.

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
from tqdm import tqdm


DEFAULT_TIMEOUT = 120

# Default buffer sizes to sweep (granular at small sizes to capture cache hierarchy)
DEFAULT_BUFFER_SIZES = [
    "1k", "2k", "4k", "8k", "16k", "32k", "64k",
    "128k", "256k", "512k",
    "1m", "2m", "4m", "8m", "16m", "32m", "64m",
    "128m", "256m", "512m", "1g",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description="MLC idle_latency sweep across buffer sizes"
    )
    parser.add_argument("--mlc-path", required=True, help="Path to MLC binary")
    parser.add_argument("--device-id", required=True, type=int, help="NUMA node ID for numactl -m")
    parser.add_argument(
        "--buffer-sizes", nargs="+", default=DEFAULT_BUFFER_SIZES,
        help=f"Buffer sizes to sweep (default: {' '.join(DEFAULT_BUFFER_SIZES)})"
    )
    parser.add_argument("--core", type=int, default=1, help="Core ID to pin to (default: 1)")
    parser.add_argument("--iterations", type=int, default=1, help="Iterations per config (default: 1)")
    parser.add_argument("--output-dir", default=".", help="Parent directory for results (default: current dir)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"Timeout in seconds per MLC run (default: {DEFAULT_TIMEOUT})")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running")
    return parser.parse_args()


def create_result_folder(parent_dir, device_id):
    """Create result folder: results/<date>/idle_latency/node<id>/"""
    date_str = datetime.now().strftime("%Y%m%d")
    folder_path = os.path.join(parent_dir, "results", date_str, "idle_latency", f"node{device_id}")
    os.makedirs(folder_path, exist_ok=True)
    return folder_path


def build_command(mlc_path, device_id, core, buffer_size):
    cmd = [
        "sudo", "numactl", "-m", str(device_id), mlc_path,
        "--idle_latency", f"-c{core}", f"-b{buffer_size}",
    ]
    return cmd


def parse_mlc_output(output):
    """Parse MLC idle_latency stdout and return (clocks, latency_ns) or (None, None).

    Handles ns, us, and ms units, converting all to nanoseconds.
    """
    match = re.search(
        r"Each iteration took\s+([\d.]+)\s+base frequency clocks\s*\(\s*([\d.]+)\s*(ns|us|ms)\)",
        output
    )
    if match:
        clocks = float(match.group(1))
        value = float(match.group(2))
        unit = match.group(3)
        unit_to_ns = {"ns": 1.0, "us": 1000.0, "ms": 1_000_000.0}
        latency_ns = value * unit_to_ns[unit]
        return clocks, latency_ns
    return None, None


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


def save_run_file(folder_path, run_num, cmd, buffer_size, iteration, stdout, stderr):
    """Save individual run output to a file."""
    filename = f"run_{run_num:03d}_b{buffer_size}_iter{iteration}.txt"
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

        # Validate core ID
        n_cpus = os.cpu_count()
        if n_cpus is not None and args.core >= n_cpus:
            print(f"ERROR: Core {args.core} does not exist. System has {n_cpus} cores (0-{n_cpus - 1}).")
            sys.exit(1)

    total_runs = len(args.buffer_sizes) * args.iterations

    print(f"MLC Idle Latency Sweep")
    print(f"  NUMA node:     {args.device_id}")
    print(f"  MLC path:      {args.mlc_path}")
    print(f"  Buffer sizes:  {args.buffer_sizes}")
    print(f"  Core:          {args.core}")
    print(f"  Iterations:    {args.iterations}")
    print(f"  Total runs:    {total_runs}")
    print()

    if args.dry_run:
        print("DRY RUN — commands that would be executed:")
        for buf in args.buffer_sizes:
            cmd = build_command(args.mlc_path, args.device_id, args.core, buf)
            print(f"  {' '.join(cmd)}")
        return

    result_folder = create_result_folder(args.output_dir, args.device_id)
    summary_csv = os.path.join(result_folder, "summary.csv")
    print(f"  Results folder: {result_folder}")
    print()

    fieldnames = [
        "run_num", "device_id", "buffer_size", "core", "iteration",
        "clocks", "latency_ns", "run_file",
    ]

    with open(summary_csv, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        run_num = 0
        all_runs = [
            (buf, iteration)
            for buf in args.buffer_sizes
            for iteration in range(1, args.iterations + 1)
        ]
        pbar = tqdm(total=len(all_runs), desc="MLC idle latency", unit="run")
        try:
            for buf, iteration in all_runs:
                run_num += 1
                cmd = build_command(args.mlc_path, args.device_id, args.core, buf)

                tqdm.write(f"\n[{run_num}/{len(all_runs)}] buffer={buf}, iter={iteration}")
                tqdm.write(f"  Command: {' '.join(cmd)}")

                stdout, stderr = run_single(cmd, args.timeout)

                if stdout:
                    tqdm.write(stdout.strip())

                run_file = save_run_file(
                    result_folder, run_num, cmd, buf, iteration,
                    stdout, stderr
                )

                pbar.update(1)

                if stdout is None:
                    continue

                clocks, latency_ns = parse_mlc_output(stdout)

                row = {
                    "run_num": run_num,
                    "device_id": args.device_id,
                    "buffer_size": buf,
                    "core": args.core,
                    "iteration": iteration,
                    "clocks": clocks,
                    "latency_ns": latency_ns,
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
