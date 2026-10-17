from flask_wtf import FlaskForm
from wtforms import BooleanField
from wtforms.validators import DataRequired


class AcceptTermsForm(FlaskForm):
    accept_terms = BooleanField(
        "Jeg accepterer brugervilkårene og privatlivspolitikken",
        validators=[DataRequired("Du skal acceptere vilkårene for at fortsætte.")],
    )
