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
#SBATCH --time=00-00:15:00     # Walltime
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
#SBATCH --time=00-00:15:00     # Walltime
#SBATCH --nodes=1 # OpenMPI can have problems with ORCA over multiple nodes sometimes, depending on your system.
#SBATCH --nodelist=c001
#SBATCH --exclusive

ORCA version: ORCA/6.1.0-f.0-OpenMPI-4.1.5
```