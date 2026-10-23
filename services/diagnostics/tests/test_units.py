import numpy as np

from app import lims
from app.segment import EnclosedLungLesionPlugin
from app.synth import chest_phantom_slice

ASTM = "\n".join([
    "H|\\^&|MSG0001||Cobas^8000|||||||P|1|20260927101500",
    "P|1||MRN-100001||Iyer^Lakshmi",
    "O|1|SPEC01||^^^K\\^^^NA",
    "R|1|^^^K|6.8|mmol/L|3.5-5.1|H||F",
    "R|2|^^^NA|138|mmol/L|135-145|N||F",
    "L|1|N",
])


def test_parse_astm():
    m = lims.parse_astm(ASTM)
    assert m.mrn == "MRN-100001"
    assert m.message_id == "MSG0001"
    assert [(r.code, r.value) for r in m.results] == [("K", 6.8), ("NA", 138.0)]


def test_parse_astm_with_framing():
    framed = "\x021" + ASTM.replace("\n", "\r") + "\x03A7"
    assert len(lims.parse_astm(framed).results) == 2


def test_parse_hl7_roundtrip():
    msg = lims.build_hl7_oru("MRN-100002", [("2823-3", "Potassium", 2.1, "mmol/L")], "CTRL9")
    m = lims.parse_hl7(msg)
    assert m.mrn == "MRN-100002" and m.message_id == "CTRL9"
    assert m.results[0].code == "2823-3" and m.results[0].code_system == "loinc"


def test_classify_ignores_analyzer_flag():
    ref = {"low": 3.5, "high": 5.1, "panic_low": 2.5, "panic_high": 6.5}
    assert lims.classify(6.8, ref) == "HH"
    assert lims.classify(5.4, ref) == "H"
    assert lims.classify(4.0, ref) == "N"
    assert lims.classify(2.4, ref) == "LL"


def test_segmentation_finds_phantom_nodule():
    rng = np.random.default_rng(1)
    hu = chest_phantom_slice(512, 0.7, 0.0, (-72.0, -20.0, 0.0, 7.5), rng)
    res = EnclosedLungLesionPlugin().run(hu, (0.7, 0.7))
    assert len(res["findings"]) == 1
    f = res["findings"][0]
    assert 12 < f["equivalent_diameter_mm"] < 17
    assert f["label"] == "pulmonary nodule candidate"


def test_segmentation_clean_lungs():
    rng = np.random.default_rng(2)
    hu = chest_phantom_slice(512, 0.7, 0.0, None, rng)
    assert EnclosedLungLesionPlugin().run(hu, (0.7, 0.7))["findings"] == []
