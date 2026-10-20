from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, SelectMultipleField, StringField, TextAreaField, widgets
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError

from ..brands import current_brand
from ..core import contact_info
from . import choices, interests
from .fields import NOT_HIDEABLE, profile_fields


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
    # Påkrævet og kan ikke skjules: kønnet bruges til at matche i begge retninger.
    gender = SelectField("Køn", choices=_blank(choices.GENDERS), validators=[DataRequired("Vælg dit køn.")])
    seeking_genders = MultiCheckboxField("Interesseret i", choices=choices.GENDERS, validators=[Optional()])
    height_cm = IntegerField("Højde i cm", validators=[Optional(), NumberRange(120, 230, "Skriv en højde mellem 120 og 230 cm.")])
    occupation = StringField("Beskæftigelse", filters=[_strip_or_none], validators=[Optional(), Length(max=80), contact_info.validator])
    education = SelectField("Uddannelse", choices=_blank(choices.EDUCATION), validators=[Optional()])

    children = SelectField("Børn", choices=_blank(choices.CHILDREN), validators=[Optional()])
    wants_children = SelectField("Ønsker børn", choices=_blank(choices.WANTS_CHILDREN), validators=[Optional()])
    exercise = SelectField("Motion", choices=_blank(choices.EXERCISE), validators=[Optional()])
    smoking = SelectField("Rygning", choices=_blank(choices.SMOKING), validators=[Optional()])
    alcohol = SelectField("Alkohol", choices=_blank(choices.ALCOHOL), validators=[Optional()])
    # Valgmulighederne sættes i __init__, fordi administrator kan ændre kataloget (profiles/interests.py).
    interest_tags = MultiCheckboxField("Interesser", choices=[], validators=[Optional()])
    interests = StringField("Mere om mine interesser", filters=[_strip_or_none],
                            validators=[Optional(), Length(max=300), contact_info.validator])
    travel = StringField("Rejser", filters=[_strip_or_none], validators=[Optional(), Length(max=300), contact_info.validator])

    # Navn og valg sættes i __init__ efter brandet: "Søger" eller "Hvad har du lyst til" (choices.LOOKING_FOR).
    relationship_types = MultiCheckboxField("Søger", choices=choices.RELATIONSHIP_TYPES, validators=[Optional()])
    age_min = IntegerField("Alder fra", validators=[Optional(), NumberRange(18, 99, "Alder skal være mellem 18 og 99.")])
    age_max = IntegerField("Alder til", validators=[Optional(), NumberRange(18, 99, "Alder skal være mellem 18 og 99.")])
    preferred_distance_km = SelectField("Afstand", choices=choices.DISTANCES, validators=[Optional()])
    long_distance_ok = SelectField(
        "Åben for langdistance", choices=[("", "Ikke oplyst"), ("ja", "Ja"), ("nej", "Nej")], validators=[Optional()]
    )
    expectations = TextAreaField("Forventninger", filters=[_strip_or_none],
                                 validators=[Optional(), Length(max=500), contact_info.validator])

    # Stjernetegnet beregnes ud fra fødselsdatoen og kan ikke redigeres, kun skjules. Valgene sættes i __init__.
    hidden_fields = MultiCheckboxField("Skjul for andre", choices=[], validators=[Optional()])

    # Formularfelterne bag datingfelterne (fields.DATING_FIELDS). Fjernes, når brandet ikke har dem (app/brands).
    DATING_FORM_FIELDS = ("wants_children", "age_min", "age_max", "preferred_distance_km", "long_distance_ok",
                          "expectations")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        brand = current_brand()
        if not brand.dating_fields:
            for name in self.DATING_FORM_FIELDS:
                delattr(self, name)
        fields = profile_fields()
        self.seeking_genders.label.text = brand.words["seeking_genders"]
        looking_for = choices.looking_for()
        self.relationship_types.label.text = looking_for.label
        self.relationship_types.choices = looking_for.choices
        self.hidden_fields.choices = ([(f.name, f.label) for f in fields if f.name not in NOT_HIDEABLE]
                                      + [("zodiac", "Stjernetegn")])
        self.interest_tags.choices = interests.choices()
        self.interest_groups = interests.catalog()

    def validate_interest_tags(self, field):
        if len(field.data or []) > interests.MAX_PER_PROFILE:
            raise ValidationError(f"Vælg højst {interests.MAX_PER_PROFILE} interesser.")

    def validate_age_max(self, field):
        if self.age_min.data and field.data and self.age_min.data > field.data:
            raise ValidationError("'Alder til' skal være mindst lige så høj som 'Alder fra'.")

    # Felter, der kopieres direkte mellem formular og profil. Tom værdi gemmes som NULL.
    SCALAR_FIELDS = ("presentation", "gender", "height_cm", "occupation", "education", "children", "wants_children",
                     "exercise", "smoking", "alcohol", "interests", "travel", "age_min", "age_max", "expectations")
    LIST_FIELDS = ("seeking_genders", "relationship_types", "hidden_fields")

    def _present(self, names):
        """Kun de felter, formularen har for det kørende brand. Fjernede felter lades urørt på profilen."""
        return [name for name in names if name in self._fields]

    def load(self, profile):
        for name in self._present(self.SCALAR_FIELDS):
            getattr(self, name).data = getattr(profile, name)
        for name in self.LIST_FIELDS:
            getattr(self, name).data = list(getattr(profile, name))
        self.interest_tags.data = list(profile.interest_tags or [])
        if "preferred_distance_km" in self._fields:
            self.preferred_distance_km.data = str(profile.preferred_distance_km or "")
        if "long_distance_ok" in self._fields:
            self.long_distance_ok.data = {True: "ja", False: "nej"}.get(profile.long_distance_ok, "")

    def save(self, profile):
        for name in self._present(self.SCALAR_FIELDS):
            setattr(profile, name, getattr(self, name).data or None)
        for name in self.LIST_FIELDS:
            setattr(profile, name, getattr(self, name).data or [])
        # Interesser, administrator har fjernet fra kataloget, vises ikke i formularen, men bevares på profilen.
        current = {key for key, _ in self.interest_tags.choices}
        profile.interest_tags = list(self.interest_tags.data or []) + [
            key for key in profile.interest_tags or [] if key not in current]
        if "preferred_distance_km" in self._fields:
            profile.preferred_distance_km = (int(self.preferred_distance_km.data) if self.preferred_distance_km.data
                                             else None)
        if "long_distance_ok" in self._fields:
            profile.long_distance_ok = {"ja": True, "nej": False}.get(self.long_distance_ok.data)


class VisibilityForm(FlaskForm):
    hidden_from_search = BooleanField("Skjul min profil fra søgninger")
    matches_only = BooleanField("")  # teksten sættes i __init__ med brandets ord
    show_kommune = BooleanField("Vis min kommune på profilen")
    show_activity = BooleanField("Vis når jeg har været aktiv for nylig")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.matches_only.label.text = (
            f"Vis kun min profil for personer, jeg har {current_brand().words['a_match']} med")

    def load(self, profile):
        for name in ("hidden_from_search", "matches_only", "show_kommune", "show_activity"):
            getattr(self, name).data = getattr(profile, name)

    def save(self, profile, include_kommune=True):
        for name in ("hidden_from_search", "matches_only", "show_kommune", "show_activity"):
            if name == "show_kommune" and not include_kommune:
                continue
            setattr(profile, name, bool(getattr(self, name).data))


class ExplorePreferenceForm(FlaskForm):
    hide_liked = BooleanField("")  # teksten sættes i __init__ med brandets ord

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        words = current_brand().words
        self.hide_liked.label.text = f"Skjul profiler, jeg har {words['liked']}, indtil de {words['like_back']}"

    def save(self, profile):
        profile.hide_liked = bool(self.hide_liked.data)
