## Setting up the OpenBQ CLI via Docker
To seamlessly wire Validex app to OpenBQ's biometric assessments locally, spinning up the Dockerized CLI is definitely the cleanest approach, as it prevents having to compile its heavy C++ dependencies.

1. Pull the official Docker image: Open your terminal and run:

```bash
docker pull ghcr.io/openbq/openbq-cli:latest
```
(Note: Sometimes they publish directly to standard hub as openbq/openbq-cli:latest if ghcr gives you any trouble).

2. Running the CLI securely with Volume Mapping: Because the Validex app receives biometric files (like fingerprints or faces) locally into its project directories, the Docker container needs to be able to reach your host directories to grade the files.

You run the container using a volume binding (-v) to mount your local folder into the container's /workspace:

```bash
# On Windows (PowerShell):
docker run -it --rm -v ${PWD}:/workspace ghcr.io/openbq/openbq-cli analyze /workspace/sample_face.jpg
```

How Validex should interact with it: In services.py, you can route your Python backend to call this Docker container in the background using the subprocess command whenever a user uploads a biometric image. You just execute that docker command programmatically, pass the temporary filepath into the /workspace/ volume mount, and then have Validex read OpenBQ's standard stdout JSON output containing the face/fingerprint quality score!