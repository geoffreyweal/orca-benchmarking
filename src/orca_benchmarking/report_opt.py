import os
import re
import subprocess
import csv
import sys

# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------
BENCH_DIR_NAME = "orca_benchmarking"

SLURM_FILE_REGEX = re.compile(r"slurm_(\d+)_(\d+)\.out")

OPT_CYCLE_REGEX = re.compile(r"GEOMETRY OPTIMIZATION CYCLE", re.IGNORECASE)


# ------------------------------------------------------------
# ORCA output parsing
# ------------------------------------------------------------
def parse_orca_output(path):
    """
    Extract wall time, CPU time, and number of geometry optimisation steps.
    """
    wall_time = None
    cpu_time = None
    opt_steps = 0

    with open(path, "r") as f:
        for line in f:
            if "TOTAL RUN TIME" in line:
                wall_time = line.split(":")[-1].strip()
            elif "TOTAL CPU TIME" in line:
                cpu_time = line.split(":")[-1].strip()
            elif OPT_CYCLE_REGEX.search(line):
                opt_steps += 1

    return wall_time, cpu_time, opt_steps


# ------------------------------------------------------------
# Time utilities
# ------------------------------------------------------------
def hms_to_seconds(hms):
    """
    Convert HH:MM:SS or H:MM:SS to seconds.
    """
    if hms is None:
        return None

    parts = [float(p) for p in hms.split(":")]
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h = 0
        m, s = parts
    else:
        return None

    return h * 3600 + m * 60 + s


# ------------------------------------------------------------
# nn_seff helpers
# ------------------------------------------------------------
def run_nn_seff(jobid, taskid):
    """
    Run nn_seff for a specific SLURM array task.
    """
    cmd = ["nn_seff", f"{jobid}_{taskid}"]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def parse_nn_seff_output(text):
    """
    Parse efficiency metrics from nn_seff output.
    """
    data = {}

    patterns = {
        "cpu_efficiency": r"CPU Efficiency:\s*([\d.]+)",
        "mem_efficiency": r"Memory Efficiency:\s*([\d.]+)",
        "cpu_util_percent": r"CPU Utilization:\s*([\d.]+)%",
        "mem_util_percent": r"Memory Utilization:\s*([\d.]+)%",
    }

    for key, regex in patterns.items():
        match = re.search(regex, text)
        if match:
            data[key] = float(match.group(1))

    return data


# ------------------------------------------------------------
# Main report logic
# ------------------------------------------------------------
def main():
    # Ensure we are running from the directory ABOVE orca_benchmarking
    cwd = os.getcwd()
    bench_dir = os.path.join(cwd, BENCH_DIR_NAME)

    if not os.path.isdir(bench_dir):
        sys.exit(
            f"\n❌ Error: '{BENCH_DIR_NAME}/' not found in:\n"
            f"    {cwd}\n\n"
            "Please run this command from the directory ABOVE "
            "'orca_benchmarking/'.\n"
        )

    results = []

    # Scan for SLURM output files
    for filename in os.listdir(bench_dir):
        match = SLURM_FILE_REGEX.match(filename)

        import pdb; pdb.set_trace()
        if not match:
            continue

        jobid, cores = match.groups()
        cores = int(cores)

        print(f"🔍 Processing OPT benchmark: cores={cores}, jobid={jobid}")

        orca_out_path = os.path.join(
            bench_dir,
            f"{cores}cores",
            "orca.out",
        )

        if not os.path.isfile(orca_out_path):
            print(f"⚠ Missing ORCA output: {orca_out_path}")
            continue

        wall_time, cpu_time, opt_steps = parse_orca_output(orca_out_path)

        wall_seconds = hms_to_seconds(wall_time)
        time_per_step = (
            wall_seconds / opt_steps
            if wall_seconds is not None and opt_steps > 0
            else None
        )

        # Run nn_seff for this array task
        try:
            seff_text = run_nn_seff(jobid, cores)
            seff_data = parse_nn_seff_output(seff_text)
        except Exception as exc:
            print(f"⚠ nn_seff failed for {jobid}_{cores}: {exc}")
            seff_data = {}

        row = {
            "cores": cores,
            "opt_steps": opt_steps,
            "wall_time": wall_time,
            "cpu_time": cpu_time,
            "time_per_step_s": time_per_step,
            **seff_data,
        }

        results.append(row)

    if not results:
        sys.exit("\n❌ No optimisation benchmark data found.\n")

    results.sort(key=lambda x: x["cores"])

    output_csv = "orca_benchmark_results_opt.csv"
    with open(output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    print(f"\n✅ Optimisation benchmark report written to {output_csv}")


# ------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------
def cli():
    main()