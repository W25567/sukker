from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, SelectMultipleField, StringField, TextAreaField, widgets
from wtforms.validators import Length, NumberRange, Optional, ValidationError

from ..core import contact_info
from . import choices
from .fields import FIELDS


class MultiCheckboxField(SelectMultipleField):
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


def _blank(options, empty="Vælg"):
    return [("", empty)] + list(options)


def _strip_or_none(value):
    if isinstance(value, str):
        value = value.strip()
    return value or None


class ProfileForm(FlaskForm):
    presentation = TextAreaField("Præsentation", filters=[_strip_or_none],
                                 validators=[Optional(), Length(max=1000), contact_info.validator])
    gender = SelectField("Køn", choices=_blank(choices.GENDERS), validators=[Optional()])
    seeking_genders = MultiCheckboxField("Interesseret i", choices=choices.GENDERS, validators=[Optional()])
    height_cm = IntegerField("Højde i cm", validators=[Optional(), NumberRange(120, 230, "Skriv en højde mellem 120 og 230 cm.")])
    occupation = StringField("Beskæftigelse", filters=[_strip_or_none], validators=[Optional(), Length(max=80), contact_info.validator])
    education = SelectField("Uddannelse", choices=_blank(choices.EDUCATION), validators=[Optional()])

    children = SelectField("Børn", choices=_blank(choices.CHILDREN), validators=[Optional()])
    wants_children = SelectField("Ønsker børn", choices=_blank(choices.WANTS_CHILDREN), validators=[Optional()])
    exercise = SelectField("Motion", choices=_blank(choices.EXERCISE), validators=[Optional()])
    smoking = SelectField("Rygning", choices=_blank(choices.SMOKING), validators=[Optional()])
    alcohol = SelectField("Alkohol", choices=_blank(choices.ALCOHOL), validators=[Optional()])
    interests = StringField("Interesser og hobbyer", filters=[_strip_or_none],
                            validators=[Optional(), Length(max=300), contact_info.validator])
    travel = StringField("Rejser", filters=[_strip_or_none], validators=[Optional(), Length(max=300), contact_info.validator])

    relationship_types = MultiCheckboxField("Søger", choices=choices.RELATIONSHIP_TYPES, validators=[Optional()])
    age_min = IntegerField("Alder fra", validators=[Optional(), NumberRange(18, 99, "Alder skal være mellem 18 og 99.")])
    age_max = IntegerField("Alder til", validators=[Optional(), NumberRange(18, 99, "Alder skal være mellem 18 og 99.")])
    preferred_distance_km = SelectField("Afstand", choices=choices.DISTANCES, validators=[Optional()])
    long_distance_ok = SelectField(
        "Åben for langdistance", choices=[("", "Ikke oplyst"), ("ja", "Ja"), ("nej", "Nej")], validators=[Optional()]
    )
    expectations = TextAreaField("Forventninger", filters=[_strip_or_none],
                                 validators=[Optional(), Length(max=500), contact_info.validator])

    # Stjernetegnet beregnes ud fra fødselsdatoen og kan ikke redigeres, kun skjules.
    hidden_fields = MultiCheckboxField("Skjul for andre", choices=[(f.name, f.label) for f in FIELDS] + [("zodiac", "Stjernetegn")],
                                       validators=[Optional()])

    def validate_age_max(self, field):
        if self.age_min.data and field.data and self.age_min.data > field.data:
            raise ValidationError("'Alder til' skal være mindst lige så høj som 'Alder fra'.")

    # Felter, der kopieres direkte mellem formular og profil. Tom værdi gemmes som NULL.
    SCALAR_FIELDS = ("presentation", "gender", "height_cm", "occupation", "education", "children", "wants_children",
                     "exercise", "smoking", "alcohol", "interests", "travel", "age_min", "age_max", "expectations")
    LIST_FIELDS = ("seeking_genders", "relationship_types", "hidden_fields")

    def load(self, profile):
        for name in self.SCALAR_FIELDS:
            getattr(self, name).data = getattr(profile, name)
        for name in self.LIST_FIELDS:
            getattr(self, name).data = list(getattr(profile, name))
        self.preferred_distance_km.data = str(profile.preferred_distance_km or "")
        self.long_distance_ok.data = {True: "ja", False: "nej"}.get(profile.long_distance_ok, "")

    def save(self, profile):
        for name in self.SCALAR_FIELDS:
            setattr(profile, name, getattr(self, name).data or None)
        for name in self.LIST_FIELDS:
            setattr(profile, name, getattr(self, name).data or [])
        profile.preferred_distance_km = int(self.preferred_distance_km.data) if self.preferred_distance_km.data else None
        profile.long_distance_ok = {"ja": True, "nej": False}.get(self.long_distance_ok.data)


class VisibilityForm(FlaskForm):
    hidden_from_search = BooleanField("Skjul min profil fra søgninger")
    matches_only = BooleanField("Vis kun min profil for personer, jeg har et match med")
    show_kommune = BooleanField("Vis min kommune på profilen")
    show_activity = BooleanField("Vis når jeg har været aktiv for nylig")

    def load(self, profile):
        for name in ("hidden_from_search", "matches_only", "show_kommune", "show_activity"):
            getattr(self, name).data = getattr(profile, name)

    def save(self, profile):
        for name in ("hidden_from_search", "matches_only", "show_kommune", "show_activity"):
            setattr(profile, name, bool(getattr(self, name).data))


class ExplorePreferenceForm(FlaskForm):
    hide_liked = BooleanField("Skjul profiler, jeg har liket, indtil de liker tilbage")

    def save(self, profile):
        profile.hide_liked = bool(self.hide_liked.data)
