from wtforms import BooleanField, Form, IntegerField, SelectField, SelectMultipleField, widgets
from wtforms.validators import NumberRange, Optional

from ..profiles import choices, interests

DISTANCE_FILTERS = [("", "Hele landet"), ("10", "Op til 10 km"), ("25", "Op til 25 km"), ("50", "Op til 50 km"),
                    ("100", "Op til 100 km"), ("200", "Op til 200 km")]


class MultiCheckboxField(SelectMultipleField):
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


class FilterForm(Form):
    """Filtre i Udforsk (specifikation 6.3). Sendes som GET, så de kan bogmærkes og bevares ved "tilbage"."""

    afstand = SelectField("Afstand", choices=DISTANCE_FILTERS, validators=[Optional()])
    alder_fra = IntegerField("Alder fra", validators=[Optional(), NumberRange(18, 99)])
    alder_til = IntegerField("Alder til", validators=[Optional(), NumberRange(18, 99)])
    koen = MultiCheckboxField("Køn", choices=choices.GENDERS, validators=[Optional()])
    soeger = MultiCheckboxField("Søger", choices=choices.RELATIONSHIP_TYPES, validators=[Optional()])
    kun_billeder = BooleanField("Kun profiler med billeder")
    # Valgmulighederne sættes i __init__, fordi administrator kan ændre kataloget (profiles/interests.py).
    interesser = MultiCheckboxField("Interesser", choices=[], validators=[Optional()])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.interesser.choices = interests.choices()
        self.interest_groups = interests.catalog()

    def criteria(self):
        """De gyldige filtre som et dict. Ugyldige værdier ignoreres i stedet for at give fejl."""
        self.validate()
        valid = lambda name: not getattr(self, name).errors  # noqa: E731
        return {
            "max_km": int(self.afstand.data) if valid("afstand") and self.afstand.data else None,
            "age_min": self.alder_fra.data if valid("alder_fra") else None,
            "age_max": self.alder_til.data if valid("alder_til") else None,
            "genders": [g for g in self.koen.data or [] if g in dict(choices.GENDERS)],
            "relationship_types": [r for r in self.soeger.data or [] if r in dict(choices.RELATIONSHIP_TYPES)],
            "photos_only": bool(self.kun_billeder.data),
            "interests": [i for i in self.interesser.data or [] if i in dict(self.interesser.choices)],
        }

    @property
    def active(self):
        c = self.criteria()
        return any([c["max_km"], c["age_min"], c["age_max"], c["genders"], c["relationship_types"], c["photos_only"],
                    c["interests"]])
