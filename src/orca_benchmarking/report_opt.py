import os
import re
import subprocess
import csv
import sys
import json

# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------
BENCH_DIR_NAME = "orca_benchmarking"

# Note: file pattern you supplied uses a hyphen
SLURM_FILE_REGEX = re.compile(r"slurm-(\d+)_(\d+)\.out")

OPT_CYCLE_REGEX = re.compile(r"GEOMETRY OPTIMIZATION CYCLE", re.IGNORECASE)


# ------------------------------------------------------------
# ORCA output parsing
# ------------------------------------------------------------
def parse_orca_output(path):
    """
    Extract ORCA-reported wall time, ORCA CPU time,
    and number of geometry optimisation steps.
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
# sacct helpers
# ------------------------------------------------------------
def run_sacct(jobid, taskid):
    """
    Run sacct using JSON output for a specific SLURM array task.
    """
    cmd = ["sacct", "--json", "-j", f"{jobid}_{taskid}"]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def parse_memory(value):
    """
    Convert memory strings like '1024K', '512M', '64G' to MB.
    """
    if not value:
        return None

    value = value.strip().upper()
    number = float(value[:-1])
    unit = value[-1]

    if unit == "K":
        return number / 1024
    elif unit == "M":
        return number
    elif unit == "G":
        return number * 1024
    else:
        return None


def parse_sacct_data(data):
    """
    Extract actual scheduler-recorded metrics:
      - elapsed time (seconds)
      - total CPU time (seconds)
      - maximum RSS (MB)
    """

    def ex_tres(objs, name, default=None, field='count'):
        tres = {m['name' if m['type']=='gres' else 'type']: m[field] for m in objs}
        return tres.get(name, default)

    jobs = data.get("jobs", [])

    if len(jobs) == 0:
        raise Exception('Error: No data for ')
    elif len(jobs) >= 2:
        raise Exception('Error: More than 1 job for ')

    job = jobs[0]

    walltime_sec = job['time']['elapsed']

    tot_cpu_msec = 0
    mem_kb = -1

    for step in job.get("steps", []):
        used = step['tres']['requested']
        tot_cpu_msec += ex_tres(used['total'], 'cpu', 0)
        lmem_kb = ex_tres(used['total'], 'mem', 0) / 1024
        if mem_kb < lmem_kb:
            mem_kb = lmem_kb

    return walltime_sec, tot_cpu_msec / 1000, mem_kb / 1024

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
    for filename in sorted(os.listdir(bench_dir)):
        match = SLURM_FILE_REGEX.match(filename)
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

        # ORCA-parsed values
        wall_time, orca_cpu_time, opt_steps = parse_orca_output(orca_out_path)

        # Scheduler-parsed values (ground truth)
        try:
            sacct_json = run_sacct(jobid, cores)
            elapsed_s, cpu_used_s, max_rss_mb = parse_sacct_data(sacct_json)
        except Exception as exc:
            print(f"⚠ sacct failed for {jobid}_{cores}: {exc}")
            elapsed_s = cpu_used_s = max_rss_mb = None

        import pdb; pdb.set_trace()

        time_per_step = (
            elapsed_s / opt_steps
            if elapsed_s is not None and opt_steps > 0
            else None
        )

        row = {
            "cores": cores,
            "opt_steps": opt_steps,
            "elapsed_time_s": elapsed_s,
            "cpu_time_s": cpu_used_s,
            "max_rss_mb": max_rss_mb,
            "orca_wall_time": wall_time,
            "orca_cpu_time": orca_cpu_time,
            "time_per_opt_step_s": time_per_step,
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