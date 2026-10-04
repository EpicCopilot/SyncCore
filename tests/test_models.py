import pytest

from anime_tracker.models import Anime


def test_model_rejects_invalid_rating():
    with pytest.raises(ValueError):
        Anime(name="Example", rating=11).validate()


def test_model_allows_unknown_total_episodes():
    Anime(name="Example", total_episodes=None).validate()
