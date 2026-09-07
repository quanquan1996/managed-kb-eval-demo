"""Shared plumbing: SDK bootstrap, stack outputs, document-name extraction.

The vector-store-free MANAGED knowledge base and the AgenticRetrieveStream API
are recent additions to the Bedrock service model. CloudShell's preinstalled
boto3 is usually too old to know about them, so this module detects the
capability and, if missing, builds a throwaway virtualenv with a newer boto3 and
re-runs the caller inside it.
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.parse

REPO = pathlib.Path(__file__).resolve().parent.parent
VENV = REPO / ".venv-sdk"
BOOTSTRAP_FLAG = "MANAGED_KB_EVAL_BOOTSTRAPPED"
MIN_BOTO3 = "boto3>=1.43.89"


def _sdk_supports_managed_kb():
    """Capability detection against the service model, never a version compare.

    Uses botocore's loader directly so no region or credentials are required.
    """
    try:
        import botocore.session

        session = botocore.session.get_session()
        control = session.get_service_model("bedrock-agent")
        kb_config = (
            control.operation_model("CreateKnowledgeBase")
            .input_shape.members["knowledgeBaseConfiguration"]
            .members
        )
        if "MANAGED" not in (kb_config["type"].enum or []):
            return False
        if "managedKnowledgeBaseConfiguration" not in kb_config:
            return False
        runtime = session.get_service_model("bedrock-agent-runtime")
        return "AgenticRetrieveStream" in runtime.operation_names
    except Exception:
        return False


def _venv_python():
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def ensure_sdk():
    """Re-exec the calling script inside a venv with a new enough boto3."""
    if _sdk_supports_managed_kb():
        return
    if os.environ.get(BOOTSTRAP_FLAG):
        sys.exit(
            "Installed boto3 still does not expose MANAGED knowledge bases or "
            "AgenticRetrieveStream. Try: pip install -U boto3"
        )

    python = _venv_python()
    if not python.exists():
        print(
            "The installed boto3 predates managed knowledge bases. Building a "
            f"local virtualenv at {VENV.name} (one-off, about 30 seconds).",
            file=sys.stderr,
        )
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
        subprocess.run(
            [str(python), "-m", "pip", "install", "-q", "--upgrade", "pip", MIN_BOTO3],
            check=True,
        )

    env = dict(os.environ, **{BOOTSTRAP_FLAG: "1"})
    # subprocess rather than os.execv: on Windows an exec'd child loses its
    # stdout while the parent still exits 0, which looks like a silent success.
    done = subprocess.run([str(python), *sys.argv], env=env)
    sys.exit(done.returncode)


def region():
    return (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-west-2"
    )


def stack_outputs(stack=None):
    """Read the stack outputs, with a readable error when the stack is absent."""
    import boto3
    import botocore.exceptions

    stack = stack or os.environ.get("STACK", "managed-kb-eval")
    cfn = boto3.client("cloudformation", region_name=region())
    try:
        described = cfn.describe_stacks(StackName=stack)["Stacks"][0]
    except botocore.exceptions.ClientError as err:
        if "does not exist" in str(err):
            sys.exit(f"Stack '{stack}' not found in {region()}. Run ./deploy.sh first.")
        raise
    return {o["OutputKey"]: o["OutputValue"] for o in described.get("Outputs", [])}


def load_questions():
    path = REPO / "sample-data" / "questions.json"
    return json.loads(path.read_text(encoding="utf-8"))["questions"]


_DOC_PATTERN = re.compile(r"[^/\"\s]+\.(?:md|txt|pdf|docx?|html?)", re.IGNORECASE)


def _basename(uri):
    """Filename from a URI, percent-decoded.

    Worth being careful here: for an S3 source the location is an https URL with
    the key percent-encoded, so a non-ASCII filename comes back as
    02-%E9%94%99... and silently fails to match any ground truth.
    """
    return urllib.parse.unquote(uri.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1])


def doc_names(result):
    """Best-effort document filename for one retrieval result.

    Retrieve returns a typed location object plus a documentId.
    AgenticRetrieveStream returns neither, only a free-form metadata map, so the
    same information has to be recovered from whichever metadata key carries it.
    _document_title is the reliable one when present.
    """
    found = set()
    metadata = result.get("metadata") or {}

    title = metadata.get("_document_title")
    if isinstance(title, str) and title:
        found.add(_basename(title))

    if not found:
        candidates = []
        location = result.get("location") or {}
        uri = (location.get("s3Location") or {}).get("uri")
        if uri:
            candidates.append(uri)
        if isinstance(result.get("documentId"), str):
            candidates.append(result["documentId"])
        for value in metadata.values():
            if isinstance(value, str):
                candidates.append(value)
            elif isinstance(value, list):
                candidates.extend(v for v in value if isinstance(v, str))

        for candidate in candidates:
            decoded = urllib.parse.unquote(candidate)
            found.update(_DOC_PATTERN.findall(decoded))

    return found


def result_text(result):
    return (result.get("content") or {}).get("text") or ""
