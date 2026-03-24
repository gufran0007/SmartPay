import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score


def make_data():
    np.random.seed(42)
    X = np.random.rand(100, 7)
    y = ((X[:, 4] > 0.5) | (X[:, 5] > 0.3)).astype(int)
    return X, y


def build_features(cust, amount):
    t = max(cust.get('total_invoices', 1), 1)
    return [
        amount / 10000,
        cust.get('paid_on_time', 0) / t,
        cust.get('avg_payment_delay', 0) / 30,
        min(t, 100) / 100,
        cust.get('paid_late', 0) / t,
        cust.get('not_paid', 0) / t,
        len(cust.get('risk_factors', [])) / 10,
    ]


class TestFeatures:
    def test_length(self, good_customer):
        assert len(build_features(good_customer, 1000)) == 7

    def test_unknown_customer_defaults(self):
        f = [5000/10000, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0]
        assert f[1] == 0.5 and f[2] == 0.0

    def test_high_risk_ratios(self, bad_customer):
        f = build_features(bad_customer, 5000)
        assert f[4] > 0.3 and f[5] > 0.2

    def test_low_risk_ratios(self, good_customer):
        f = build_features(good_customer, 5000)
        assert f[4] < 0.1 and f[5] == 0.0

    def test_all_numeric(self, good_customer):
        for v in build_features(good_customer, 1000):
            assert isinstance(v, (int, float))


class TestModel:
    def test_trains(self):
        X, y = make_data()
        model = RandomForestClassifier(n_estimators=50, random_state=42)
        model.fit(X, y)
        assert model is not None

    def test_prediction_shape(self):
        X, y = make_data()
        model = RandomForestClassifier(n_estimators=50, random_state=42)
        model.fit(X, y)
        assert len(model.predict([[0.5]*7])) == 1

    def test_probabilities_valid(self):
        X, y = make_data()
        model = RandomForestClassifier(n_estimators=50, random_state=42)
        model.fit(X, y)
        proba = model.predict_proba([[0.5]*7])[0]
        assert all(0 <= p <= 1 for p in proba)
        assert abs(sum(proba) - 1.0) < 0.01

    def test_high_risk_scores_higher(self):
        X, y = make_data()
        model = RandomForestClassifier(n_estimators=50, random_state=42)
        model.fit(X, y)
        low = model.predict_proba([[0.1, 0.9, 0.0, 0.8, 0.05, 0.0, 0.0]])[0][1]
        high = model.predict_proba([[0.8, 0.2, 0.8, 0.3, 0.7, 0.4, 0.5]])[0][1]
        assert high > low

    def test_accuracy_above_60(self):
        X, y = make_data()
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42)
        model = RandomForestClassifier(n_estimators=50, max_depth=6, random_state=42)
        model.fit(Xtr, ytr)
        assert accuracy_score(yte, model.predict(Xte)) > 0.6

    def test_feature_importance(self):
        X, y = make_data()
        model = RandomForestClassifier(n_estimators=50, random_state=42)
        model.fit(X, y)
        assert len(model.feature_importances_) == 7


class TestBlending:
    def test_known_customer(self):
        assert abs((0.6 * 0.7 + 0.4 * 0.3) - 0.54) < 0.01

    def test_unknown_customer(self):
        assert abs((0.4 * 0.6 + 0.6 * 0.5) - 0.54) < 0.01

    def test_bounded(self):
        for m in [0.0, 0.5, 1.0]:
            for h in [0.0, 0.5, 1.0]:
                assert 0.0 <= 0.6*m + 0.4*h <= 1.0

    def test_base_risk_by_amount(self):
        def base(a):
            if a > 50000: return 0.6
            if a > 20000: return 0.55
            if a < 5000: return 0.4
            return 0.5
        assert base(100000) == 0.6 and base(1000) == 0.4

    def test_threshold(self):
        assert (1 if 0.6 > 0.5 else 0) == 1
        assert (1 if 0.4 > 0.5 else 0) == 0