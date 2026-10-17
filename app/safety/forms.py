from flask_wtf import FlaskForm
from wtforms import BooleanField, RadioField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from ..moderation.models import REASONS


class ReportForm(FlaskForm):
    reason = RadioField("Hvad drejer det sig om?", choices=REASONS, validators=[DataRequired("Vælg en årsag.")])
    description = TextAreaField("Beskriv kort, hvad der er sket", validators=[Optional(), Length(max=1000)])
    also_hide = BooleanField("Skjul også personen for mig")
    also_block = BooleanField("Blokér også personen, så I ikke kan kontakte hinanden")

    def validate(self, extra_validators=None):
        # Optional() på beskrivelsen stopper feltets egne validatorer, når det er tomt.
        # Derfor kontrolleres kravet ved "Andet" her.
        valid = super().validate(extra_validators)
        if self.reason.data == "andet" and not (self.description.data or "").strip():
            self.description.errors = [*self.description.errors, "Beskriv kort, hvad det drejer sig om."]
            return False
        return valid
