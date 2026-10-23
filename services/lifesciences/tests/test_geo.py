from app.geo import geofence_decision, haversine_m, relevance


def test_haversine_short_distance():
    d = haversine_m(12.9716, 77.5946, 12.9725, 77.5946)
    assert 95 < d < 105


def test_geofence_inside_and_accuracy():
    assert geofence_decision(40, 150, 20)[0]
    assert not geofence_decision(140, 150, 20)[0], "accuracy circle crossing the fence must fail"
    assert not geofence_decision(10, 150, 250)[0], "coarse fixes must fail"
    assert not geofence_decision(2000, 150, 5)[0]


def test_relevance_prefers_matching_specialty():
    ckd = relevance(["ckd", "hypertension", "internal medicine"], ["ckd"], ["renal outcomes"], "Internal Medicine", {"ckd": 0.2})
    hf = relevance(["heart failure", "cardiology"], ["ckd"], ["renal outcomes"], "Internal Medicine", {"ckd": 0.2})
    assert ckd > hf
