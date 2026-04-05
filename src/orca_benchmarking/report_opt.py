import os
import re
import subprocess
import csv

BENCH_DIR = "orca_benchmarking"

SLURM_REGEX = re.compile(r"slurm_(\d+)_(\d+)\.out")

def parse_orca_output(path):
    wall_time = None
    cpu_time = None

    with open(path) as f:
        for line in f:
            if "TOTAL RUN TIME" in line:
                wall_time = line.split(":")[-1].strip()
            elif "TOTAL CPU TIME" in line:
                cpu_time = line.split(":")[-1].strip()

    return wall_time, cpu_time


def run_nn_seff(jobid, taskid):
    cmd = ["nn_seff", f"{jobid}_{taskid}"]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout


def parse_nn_seff_output(text):
    data = {}

    patterns = {
        "cpu_util_percent": r"CPU Utilization:\s*([\d.]+)%",
        "mem_util_percent": r"Memory Utilization:\s*([\d.]+)%",
        "cpu_efficiency": r"CPU Efficiency:\s*([\d.]+)",
        "mem_efficiency": r"Memory Efficiency:\s*([\d.]+)"
    }

    for key, pattern in patterns.items():
        m = re.search(pattern, text)
        if m:
            data[key] = float(m.group(1))

    return data


def main():
    if not os.path.isdir(BENCH_DIR):
        raise RuntimeError(f"Expected benchmark directory '{BENCH_DIR}' not found")

    results = []

    for fname in os.listdir(BENCH_DIR):
        match = SLURM_REGEX.match(fname)
        if not match:
            continue

        jobid, cores = match.groups()
        cores = int(cores)

        print(f"🔍 Processing cores={cores}, job={jobid}")

        orca_out = os.path.join(BENCH_DIR, f"{cores}cores", "orca.out")
        if not os.path.isfile(orca_out):
            print(f"⚠ Missing ORCA output: {orca_out}")
            continue

        wall_time, cpu_time = parse_orca_output(orca_out)

        try:
            seff_text = run_nn_seff(jobid, cores)
            seff_data = parse_nn_seff_output(seff_text)
        except Exception as e:
            print(f"⚠ nn_seff failed for {jobid}_{cores}: {e}")
            seff_data = {}

        row = {
            "cores": cores,
            "wall_time": wall_time,
            "cpu_time": cpu_time,
            **seff_data
        }

        results.append(row)

    results.sort(key=lambda x: x["cores"])

    output_csv = "orca_benchmark_results.csv"
    with open(output_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)

    print(f"\n✅ Benchmark report written to {output_csv}")


if __name__ == "__main__":
    main()