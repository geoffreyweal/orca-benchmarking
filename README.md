# ORCA Benchmarking Toolkit

A command-line tool for generating **SLURM array jobs** and input directories
to benchmark **ORCA** quantum chemistry calculations across CPU core counts.

## Features

- SLURM array jobs (`--array=64-128%4`)
- Per-core ORCA input generation
- Automatic `%PAL NPROCS` and `%MAXCORE` patching
- TMPDIR staging with persistent output handling
- Clean, documented `submit_array.sl` generation

## Installation (from GitHub)

```bash
pip install git+https://github.com/geoffreyweal/orca-benchmarking.git
```

## Test

Test with:

```sh
orca-benchmark orca.inp ORCA_benchmarking_submit_include.txt --cores 1 --mem-per-cpu=2000MB --copy-data ./orca_data_to_copy
```

or (as you may need more memory),

```sh
orca-benchmark orca.inp ORCA_benchmarking_submit_include.txt --cores 10 --mem-per-cpu=2000MB --copy-data ./orca_data_to_copy
```

where `ORCA_benchmarking_submit_include.txt` is

```sh
#SBATCH --time=00-00:15:00 # Walltime
#SBATCH --nodes=1 # OpenMPI can have problems with ORCA over multiple nodes sometimes, depending on your system.
# #SBATCH --nodelist=c001
# #SBATCH --exclusive
ORCA version: ORCA/6.1.0-f.0-OpenMPI-4.1.5
```

## Usage

Once you are ready:

```sh
orca-benchmark orca.inp ORCA_benchmarking_submit_include.txt --cores 1-16,18-32%2,34-48%4,64-166%8 --mem-per-cpu=2000MB --copy-data ./orca_data_to_copy
```

where `ORCA_benchmarking_submit_include.txt` is

```sh
#SBATCH --time=00-00:15:00 # Walltime
#SBATCH --nodes=1 # OpenMPI can have problems with ORCA over multiple nodes sometimes, depending on your system.
#SBATCH --nodelist=c001
#SBATCH --exclusive
ORCA version: ORCA/6.1.0-f.0-OpenMPI-4.1.5
```

---

## Analysing Benchmark Results

After the ORCA benchmarking jobs have completed, an interactive performance
report can be generated using the provided analysis script:

```
report_opt.py
```

This script parses ORCA output files together with SLURM accounting data to
create a Plotly figure showing performance scaling with
CPU core count.

### Directory layout requirement

The script must be run from the directory **above** `orca_benchmarking/`.
The expected layout is:

```
.
└── orca_benchmarking/
    ├── slurm-<jobid>_<cores>.out
    ├── 1cores/orca.out
    ├── 2cores/orca.out
    ├── 4cores/orca.out
    └── ...
```

- Each `slurm-<jobid>_<cores>.out` corresponds to a completed SLURM array task.
- Each `<N>cores/orca.out` file contains the ORCA output for that core count.

### Generating the report

Run the script with:

```bash
python report_opt.py
```

During execution the program will:

- Locate the benchmark directory
- Parse ORCA optimisation timing data
- Query SLURM job statistics via `sacct`
- Display progress using a terminal progress bar
- Generate an interactive HTML report

### Output

On success, the following file is written:

```
orca_benchmark_results_opt.html
```

The report contains:

- A **physically square**, browser‑responsive figure
- Four subplots showing:
  - CPU efficiency (0–100%)
  - Mean DIIS iteration time
  - Mean SOSCF iteration time
  - Mean geometry optimisation iteration time
- Axes starting at **(0,0)** on all plots
- Full axis lines, ticks, labels, and trace legends
- Synchronized hover comparisons across core counts

The figure automatically resizes to fill as much of the browser window as
possible while remaining perfectly square.

### Optional CSV output

To additionally write the aggregated benchmark data to CSV:

```bash
python report_opt.py --csv
```

This will also generate:

```
orca_benchmark_results_opt.csv
```

containing one row per core count with efficiency and timing metrics.
