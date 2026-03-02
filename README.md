# MLC Experiments

Automated benchmarking toolkit for [Intel Memory Latency Checker (MLC)](https://www.intel.com/content/www/us/en/developer/articles/tool/intelr-memory-latency-checker.html). Sweeps `--max_bandwidth` across buffer sizes and core counts on a target NUMA node, then post-processes results into an Excel report.

## Prerequisites

- Intel MLC binary (v3.11b or later)
- `numactl`
- `sudo` access (passwordless recommended)
- Python 3.10+

### Environment Setup

```bash
conda create -n mlc_3p12 python=3.12 -y
conda activate mlc_3p12
pip install -r requirements.txt
```

## Usage

### 1. Run the Bandwidth Sweep

```bash
conda activate mlc_3p12

# Full sweep (10 buffer sizes × 6 core counts = 60 runs)
python mlc-bw-sweep.py \
    --mlc-path /path/to/mlc \
    --device-id 0

# Custom sweep with multiple iterations
python mlc-bw-sweep.py \
    --mlc-path /path/to/mlc \
    --device-id 0 \
    --buffer-sizes 128m 512m 1g 4g \
    --core-counts 1 4 8 16 \
    --iterations 3

# Dry run (print commands without executing)
python mlc-bw-sweep.py \
    --mlc-path /path/to/mlc \
    --device-id 0 \
    --dry-run
```

#### Sweep Options

| Option           | Default                                  | Description                     |
|------------------|------------------------------------------|---------------------------------|
| `--mlc-path`     | *(required)*                             | Path to MLC binary              |
| `--device-id`    | *(required)*                             | NUMA node ID for `numactl -m`   |
| `--buffer-sizes` | `64m 128m 256m 512m 1g 2g 4g 8g 16g 32g` | Buffer sizes to sweep           |
| `--core-counts`  | `1 2 4 8 16 32`                          | Core counts to sweep            |
| `--start-core`   | `1`                                      | Starting core ID for the range  |
| `--iterations`   | `1`                                      | Number of iterations per config |
| `--output-dir`   | `.`                                      | Parent directory for results    |
| `--timeout`      | `600`                                    | Timeout in seconds per MLC run  |
| `--dry-run`      | `false`                                  | Print commands without running  |

### 2. Post-Process Results

```bash
python mlc-postprocess.py --results-dir results/20260301/max_bw/node0
```

This generates `summary.xlsx` in the results folder with:

- **Raw Data** — per-config blocks with all iteration values
- **Summary** — aggregated statistics (mean, std, min, max) per config
- **Pivot Tables** — one table per metric (ALL Reads, 3:1 RW, 2:1 RW, 1:1 RW, Stream-triad) with buffer sizes as rows and core counts as columns

## Output Structure

```
results/
  20260301/
    max_bw/
      node0/
        summary.csv             # Raw results in CSV
        summary.xlsx            # Post-processed Excel report
        run_001_b128m_k1-1_iter1.txt
        run_002_b128m_k1-8_iter1.txt
        ...
```

## Metrics Collected

Each MLC `--max_bandwidth` run reports bandwidth (MB/s) for five read-write patterns:

| Pattern           | Description                       |
|-------------------|-----------------------------------|
| ALL Reads         | 100% read traffic                 |
| 3:1 Reads-Writes  | 75% reads, 25% writes             |
| 2:1 Reads-Writes  | 67% reads, 33% writes             |
| 1:1 Reads-Writes  | 50% reads, 50% writes             |
| Stream-triad like | STREAM triad (a = b + scalar × c) |
