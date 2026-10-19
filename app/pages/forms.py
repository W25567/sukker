from flask_wtf import FlaskForm
from wtforms import BooleanField
from wtforms.validators import DataRequired


class PrereleaseForm(FlaskForm):
    understood = BooleanField(
        "Jeg forstår, at Sukker.nu er i pre-release og endnu ikke officielt i luften",
        validators=[DataRequired("Sæt kryds for at gå videre.")],
    )


class AcceptTermsForm(FlaskForm):
    accept_terms = BooleanField(
        "Jeg accepterer brugervilkårene og privatlivspolitikken",
        validators=[DataRequired("Du skal acceptere vilkårene for at fortsætte.")],
    )
