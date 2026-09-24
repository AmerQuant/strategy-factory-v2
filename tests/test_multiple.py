import numpy as np

from sfactory.stats.multiple import benjamini_hochberg, pbo_cscv


def test_benjamini_hochberg_textbook():
    assert benjamini_hochberg([0.01, 0.04, 0.03, 0.2], q=0.05).tolist() == [True, False, False, False]
    assert benjamini_hochberg([0.001, 0.008, 0.039, 0.041, 0.6], q=0.05).tolist() == [True, True, False, False, False]
    assert not benjamini_hochberg([0.5, 0.9]).any()


def test_pbo_high_for_pure_noise_low_for_real_winner():
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 1, (1200, 20))
    assert 0.3 < pbo_cscv(noise, 10)["pbo"] < 0.8
    winner = noise.copy()
    winner[:, 3] += 0.25
    assert pbo_cscv(winner, 10)["pbo"] < 0.1
