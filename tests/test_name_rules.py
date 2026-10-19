"""Tests for reglerne for viste navne: ingen administrationsnavne, grove eller seksuelle ord, men heller ikke
afvisning af almindelige navne, der tilfældigvis indeholder et af ordene."""
import pytest

from app.core.name_rules import problem

from .conftest import register


@pytest.mark.parametrize("name", [
    "Admin", "Administrator", "SuperAdmin", "Moderator", "Sukker.nu", "Sukker Nu", "Support Team",
])
def test_reserved_names(name):
    assert problem(name) == "reserved"


@pytest.mark.parametrize("name", [
    "Fisse", "Fissefar", "Pik", "Lille Pik", "SuperPik", "P1k", "Røv", "Røvhul", "Rovhul", "Pikhoved", "Lort",
    "Fiiisse", "f.i.s.s.e", "F i s s e", "Porno", "Pornostjernen", "Playboy", "PlayBoy69", "Sexy Sofie", "Sexet",
    "Hornyboy", "OnlyFans Anna", "Luder", "Kælling", "Fuck", "Nazi", "Hitler88", "Pikslikker",
])
def test_offensive_names(name):
    assert problem(name) == "offensive", name


@pytest.mark.parametrize("name", [
    "Michelle", "Hellen", "Shelly", "Rochelle", "Pikachu", "Pia", "Pikke", "Nazir", "Nazia", "Nazim", "Essex",
    "Thorny", "Yoshitaka", "Lu Derek", "Ad Mina Hansen", "Peter", "Anne-Sofie", "Sebastian", "Fisseha", "Røvig",
    "Kim", "Malik", "Analise", "Charlotte", "Dickens", "Hancock", "Scunthorpe", "Sussex", "Ludvig", "Mette",
    "Pernille", "Kasper", "Jens Peter", "Ida Rovsing", "Ann", "Mus", "Sukkertop",
])
def test_ordinary_names_are_allowed(name):
    assert problem(name) is None, name


def test_registration_and_name_change_use_the_rules(app, outbox, ctx):
    client = app.test_client()
    response = register(client, display_name="Pornostjernen", email="a@example.com")
    assert "grove, krænkende eller seksuelle ord" in response.get_data(as_text=True)
    response = register(client, display_name="Administrator", email="a@example.com")
    assert "ligne et navn fra Sukker.nu" in response.get_data(as_text=True)
    register(client, display_name="Michelle", email="michelle@example.com")
    from .test_zodiac_and_admin import make_user, user

    assert user("Michelle") is not None and user("Pornostjernen") is None and user("Administrator") is None

    anna = make_user(app, outbox, "Anna")
    response = anna.post("/konto/navn", data={"display_name": "Sexy Anna"})
    assert response.status_code == 400 and "seksuelle ord" in response.get_data(as_text=True)
