import os
import re
import subprocess
import csv
import sys
import json
import statistics

# ------------------------------------------------------------
# Constants
# ------------------------------------------------------------
BENCH_DIR_NAME = "orca_benchmarking"

# NeSI-style SLURM output files
SLURM_FILE_REGEX = re.compile(r"slurm-(\d+)_(\d+)\.out")

# ------------------------------------------------------------
# ORCA output parsing
# ------------------------------------------------------------
def parse_orca_output(path):
    """
    Extract timing statistics from ORCA output:
      - DIIS iteration times (mean, std)
      - SOSCF iteration times (mean, std)
      - Geometry iteration times (mean, std)

    Returns:
      diis_mean, diis_std,
      soscf_mean, soscf_std,
      geom_iter_mean, geom_iter_std
    """

    diis_times = []
    soscf_times = []
    geom_iter_times = []

    in_diis = False
    in_soscf = False

    iter_line_re = re.compile(r"^\s*\d+.*\s+([0-9]+(?:\.[0-9]+)?)\s*$")
    geom_iter_re = re.compile(
        r"Time for complete geometry iter\s*:\s*([0-9]+(?:\.[0-9]+)?)"
    )

    with open(path, "r") as f:
        for line in f:

            # Detect DIIS / SOSCF sections
            if "D-I-I-S" in line:
                in_diis = True
                in_soscf = False
                continue

            if "S-O-S-C-F" in line:
                in_diis = False
                in_soscf = True
                continue

            # Geometry iteration timing
            g = geom_iter_re.search(line)
            if g:
                geom_iter_times.append(float(g.group(1)))
                continue

            # Skip separator lines
            if line.strip().startswith("---"):
                continue

            # Iteration timing rows
            m = iter_line_re.match(line)
            if not m:
                continue

            time_sec = float(m.group(1))
            if in_diis:
                diis_times.append(time_sec)
            elif in_soscf:
                soscf_times.append(time_sec)

    def mean_std(values):
        if len(values) == 0:
            return None, None
        if len(values) == 1:
            return values[0], 0.0
        return statistics.mean(values), statistics.stdev(values)

    diis_mean, diis_std = mean_std(diis_times)
    soscf_mean, soscf_std = mean_std(soscf_times)
    geom_iter_mean, geom_iter_std = mean_std(geom_iter_times)

    return (
        diis_mean, diis_std,
        soscf_mean, soscf_std,
        geom_iter_mean, geom_iter_std,
    )


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


# ------------------------------------------------------------
# REQUIRED sacct parsing method (UNCHANGED LOGIC)
# ------------------------------------------------------------
def parse_sacct_data(data):
    """
    Extract actual scheduler-recorded metrics:
      - elapsed time (seconds)
      - total CPU time (seconds)
      - maximum RSS (MB)
    """

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

    elapsed_sec = job["time"]["elapsed"]

    total_cpu_msec = 0
    max_mem_b = -1

    for step in job.get("steps", []):
        tres_used = step["tres"]["requested"]

        total_cpu_msec += extract_tres(tres_used["total"], "cpu", 0)

        mem_b = extract_tres(tres_used["total"], "mem", 0)
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
            bench_dir, f"{cores}cores", "orca.out"
        )

        if not os.path.isfile(orca_out):
            print(f"⚠ Missing ORCA output: {orca_out}")
            continue

        (
            diis_mean, diis_std,
            soscf_mean, soscf_std,
            geom_iter_mean, geom_iter_std,
        ) = parse_orca_output(orca_out)

        try:
            sacct_json = run_sacct(jobid, cores)
            elapsed_s, cpu_used_s, max_rss_mb = parse_sacct_data(sacct_json)
        except Exception as exc:
            print(f"⚠ sacct failed for {jobid}_{cores}: {exc}")
            elapsed_s = cpu_used_s = max_rss_mb = None

        row = {
            "cores": cores,
            "elapsed_time_s": elapsed_s,
            "cpu_time_s": cpu_used_s,
            "max_rss_mb": max_rss_mb,
            "diis_mean_s": diis_mean,
            "diis_std_s": diis_std,
            "soscf_mean_s": soscf_mean,
            "soscf_std_s": soscf_std,
            "geom_iter_mean_s": geom_iter_mean,
            "geom_iter_std_s": geom_iter_std,
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
    