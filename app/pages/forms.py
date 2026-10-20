from flask_wtf import FlaskForm
from wtforms import BooleanField
from wtforms.validators import DataRequired


class PrereleaseForm(FlaskForm):
    understood = BooleanField("", validators=[DataRequired("Sæt kryds for at gå videre.")])

    def __init__(self, *args, **kwargs):
        from ..brands import current_brand

        super().__init__(*args, **kwargs)
        self.understood.label.text = (f"Jeg forstår, at {current_brand().name} er i pre-release og endnu ikke "
                                      "officielt i luften")


class AcceptTermsForm(FlaskForm):
    accept_terms = BooleanField(
        "Jeg accepterer brugervilkårene og privatlivspolitikken",
        validators=[DataRequired("Du skal acceptere vilkårene for at fortsætte.")],
    )
