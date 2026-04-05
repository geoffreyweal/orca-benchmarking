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

SLURM_FILE_REGEX = re.compile(r"slurm-(\d+)_(\d+)\.out")
OPT_CYCLE_REGEX = re.compile(r"GEOMETRY OPTIMIZATION CYCLE", re.IGNORECASE)

# ------------------------------------------------------------
# ORCA output parsing
# ------------------------------------------------------------
def parse_orca_output(path):
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
# sacct helpers
# ------------------------------------------------------------
def run_sacct(jobid, taskid):
    cmd = ["sacct", "--json", "-j", f"{jobid}_{taskid}"]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


# ------------------------------------------------------------
# REQUIRED sacct parsing method (UNMODIFIED LOGIC)
# ------------------------------------------------------------
def parse_sacct_data(data):
    """
    Extract actual scheduler-recorded metrics:
      - elapsed time (seconds)
      - total CPU time (seconds)
      - maximum RSS (MB)
    """

    # Helper to extract TRES values
    def extract_tres(objs, name, default=0):
        for obj in objs:
            if obj.get("type") == name:
                return obj.get("count", default)
        return default

    jobs = data.get("jobs", [])

    if len(jobs) == 0:
        raise RuntimeError("sacct returned no job data")
    if len(jobs) > 1:
        raise RuntimeError("sacct returned multiple jobs unexpectedly")

    job = jobs[0]

    # Elapsed time in seconds (ground truth runtime)
    elapsed_sec = job['time']['elapsed']

    total_cpu_msec = 0
    max_mem_b = -1

    for step in job.get("steps", []):

        tres_used = step['tres']['requested']

        # CPU time in milliseconds
        total_cpu_msec += extract_tres(tres_used['total'], "cpu", 0)

        # Memory usage in bytes (take max across steps)
        mem_b = extract_tres(tres_used['total'], "mem", 0)
        if mem_b > max_mem_b:
            max_mem_b = mem_b

    cpu_time_sec = total_cpu_msec / 1000.0
    max_rss_mb = max_mem_b / 1024.0 / 1024.0

    return elapsed_sec, cpu_time_sec, max_rss_mb


# ------------------------------------------------------------
# Main report logic
# ------------------------------------------------------------
def main():
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

    for filename in sorted(os.listdir(bench_dir)):
        match = SLURM_FILE_REGEX.match(filename)
        if not match:
            continue

        jobid, cores = match.groups()
        cores = int(cores)

        print(f"🔍 Processing OPT benchmark: cores={cores}, jobid={jobid}")

        orca_out = os.path.join(
            bench_dir,
            f"{cores}cores",
            "orca.out"
        )

        if not os.path.isfile(orca_out):
            print(f"⚠ Missing ORCA output: {orca_out}")
            continue

        wall_time, orca_cpu_time, opt_steps = parse_orca_output(orca_out)

        try:
            sacct_json = run_sacct(jobid, cores)
            elapsed_s, cpu_used_s, max_rss_mb = parse_sacct_data(sacct_json)
        except Exception as exc:
            print(f"⚠ sacct failed for {jobid}_{cores}: {exc}")
            elapsed_s = cpu_used_s = max_rss_mb = None

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