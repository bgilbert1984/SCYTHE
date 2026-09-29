"""Build a measurement copy of kaggle/biohub-harmonic-submit with the validator on.

Upstream ships with BIOHUB_VALIDATOR_ENABLE=0. Turning it on runs the pipeline on
4 held-out train movies per embryo (preferring ones with GT divisions) and scores
edge Jaccard, division Jaccard, and 7 post-process candidates, writing
validator_results.csv and ppsweep_results.csv. Everything else is unchanged.
"""

import json
from pathlib import Path

HERE = Path(__file__).parent
SOURCE = HERE.parent / "biohub-harmonic-submit" / "biohub-harmonic-submit.ipynb"
OFF = 'os.environ["BIOHUB_VALIDATOR_ENABLE"] = "0"'
ON = 'os.environ["BIOHUB_VALIDATOR_ENABLE"] = "1"'


def main() -> None:
    nb = json.loads(SOURCE.read_text())
    cell0 = "".join(nb["cells"][0]["source"])
    assert cell0.count(OFF) == 1, "validator switch not found in cell 0"
    nb["cells"][0]["source"] = cell0.replace(OFF, ON).splitlines(keepends=True)
    (HERE / "biohub-harmonic-measure.ipynb").write_text(json.dumps(nb, indent=1))


if __name__ == "__main__":
    main()
