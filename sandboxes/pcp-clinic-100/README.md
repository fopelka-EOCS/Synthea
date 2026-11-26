# Sandbox: 100-PCP adult primary care clinic

Population-level synthetic **clinical** test data (FHIR R4, US Core). Cost and claims output is out of scope and is not used.

## Design (set by the Principal, 2026-11-26)

- 100 PCPs. Panel sizes cluster around 2,000 within a range of 1,200 to 2,800, which puts the clinic at about 200,000 patients.
- Adults only.
- Each patient has 1 to 10 years of service with the clinic (time with the clinic, not age).

## Assumptions not yet approved by the Principal

- Panel sizes: normal distribution, mean 2,000, standard deviation 350, cut off at 1,200 and 2,800.
- Years of service: uniform between 1 and 10 years.
- Adult age range: 18 to 100.
- Location: Synthea's default, Massachusetts, because no location was specified.
- Carried-over history, kept even when it predates the patient's start with the clinic: conditions, allergies, immunizations, and active medications, care plans and care teams. See `assign_panels.py`.

## Requirements

JDK 17 or newer (Synthea's README; `build.gradle` sets `sourceCompatibility = '17'`), and Python 3 using the standard library only.

## Run

From the repo root (Windows Git Bash shown; `java` must be JDK 17 or newer):

```bash
./gradlew uberJar
# Pilot: one 2,000-patient panel
java -jar build/libs/synthea-with-dependencies.jar -s 20261126 -cs 20261126 -r 20261126 \
  -p 2000 -a 18-100 -c sandboxes/pcp-clinic-100/synthea.properties
python sandboxes/pcp-clinic-100/assign_panels.py --pilot \
  --input output/pcp-clinic-100/fhir --out output/pcp-clinic-100-clinic \
  --reference-date 2026-11-26
```

To build the full clinic (199,337 patients with the default seed), use the resumable batch builder:

```bash
JAVA=/path/to/java bash sandboxes/pcp-clinic-100/build_clinic.sh
```

Finished batches go to `E:/Synthea-Sandboxes/pcp-clinic-100` (override with `OUT=`), in about 260 GB in total. Raw batches are temporary: they sit under `./output/` and are deleted once each batch has been trimmed and its patient count checked.

To run the steps by hand instead, drop `--pilot` and set `-p` to at least the panel target total. With the default seed, `assign_panels.py` reports that total in `summary.json` (`panel_target_total`). Generate a few percent more patients than the target; any extra patients are left unassigned and reported.

The seeds (`-s`, `-cs`), the reference date (`-r`) and `--seed` fix the output, so a run with the same inputs produces the same population.

## Output (`output/`, git-ignored)

- `patients/`: one trimmed FHIR transaction bundle per patient, with `Patient.generalPractitioner` set to a conditional reference to that patient's PCP.
- `clinic-practitioners.json`: the clinic Organization and its PCPs. Load this first so the conditional references resolve.
- `roster.csv`: one row per patient, giving the PCP, the clinic start date, the years of service, and the number of encounters kept and dropped.
- `panels.csv`: each PCP's target panel size against the number of patients actually assigned.
- `summary.json`: the run parameters and counts.
