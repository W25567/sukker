from datetime import date

from flask_wtf import FlaskForm
from markupsafe import Markup, escape
from wtforms import BooleanField, EmailField, Field, PasswordField, SelectField, SelectMultipleField, StringField, widgets
from wtforms.validators import DataRequired, Email, EqualTo, Length, Regexp, StopValidation, ValidationError

from ..core import contact_info, name_rules
from ..core.timeutil import age_on, today_local
from ..profiles import choices

# Vist navn: behøver ikke være unikt. Bogstaver (også æøå og accenter), tal og enkelte skilletegn.
DISPLAY_NAME_PATTERN = r"^[^\W_][\w .'\-]{0,28}[^\W_]$"  # starter og slutter med bogstav eller tal
MIN_AGE = 18
PASSWORD_MIN = 10
PASSWORD_MAX = 128


def _strip(value):
    return value.strip() if isinstance(value, str) else value


def normalize_display_name(value):
    """Fjerner mellemrum i enderne og samler gentagne mellemrum."""
    return " ".join(value.split()) if isinstance(value, str) else value


def _lower(value):
    return value.strip().lower() if isinstance(value, str) else value


MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september", "oktober",
          "november", "december"]
OLDEST_AGE = 100


class BirthdateWidget:
    """Tre rullelister (dag, måned, år) i stedet for en datovælger, der er besværlig på mobil, når man skal
    langt tilbage i tiden. Alle tre hedder det samme, så feltet modtager [dag, måned, år]."""

    def __call__(self, field, **kwargs):
        chosen = [str(v) for v in field.raw_data] if field.raw_data and len(field.raw_data) == 3 else (
            [str(field.data.day), str(field.data.month), str(field.data.year)] if field.data else ["", "", ""])
        this_year = today_local().year
        parts = [
            ("Dag", "bday-day", [(str(d), str(d)) for d in range(1, 32)]),
            ("Måned", "bday-month", [(str(i), name) for i, name in enumerate(MONTHS, start=1)]),
            ("År", "bday-year", [(str(y), str(y)) for y in range(this_year - MIN_AGE, this_year - OLDEST_AGE - 1, -1)]),
        ]
        invalid = ' aria-invalid="true"' if kwargs.get("aria-invalid") else ""
        html = [f'<div class="date-selects" role="group" aria-label="{escape(field.label.text)}">']
        for index, (label, autocomplete, options) in enumerate(parts):
            ident = f' id="{escape(field.id)}"' if index == 0 else ""
            html.append(f'<select name="{escape(field.name)}"{ident} aria-label="{label}" autocomplete="{autocomplete}"{invalid}>')
            html.append(f'<option value="">{label}</option>')
            for value, text in options:
                selected = " selected" if chosen[index] == value else ""
                html.append(f'<option value="{value}"{selected}>{escape(text)}</option>')
            html.append("</select>")
        html.append("</div>")
        return Markup("".join(html))


class BirthdateField(Field):
    """Fødselsdato fra tre rullelister. Tager også en ISO-dato (ÅÅÅÅ-MM-DD), fx fra tests og ældre formularer."""

    widget = BirthdateWidget()

    def process_formdata(self, valuelist):
        self.data = None
        values = [v.strip() for v in valuelist if isinstance(v, str)]
        if not values or not any(values):
            return
        try:
            if len(values) == 1:
                self.data = date.fromisoformat(values[0])
            elif len(values) == 3:
                if not all(values):
                    raise ValueError("Vælg dag, måned og år.")
                day, month, year = (int(v) for v in values)
                try:
                    self.data = date(year, month, day)
                except ValueError:
                    raise ValueError("Den dato findes ikke. Tjek dag og måned.")
            else:
                raise ValueError("Vælg din fødselsdato.")
        except ValueError as error:
            self.data = None
            message = str(error)
            raise ValueError(message if message.startswith(("Vælg", "Den dato")) else "Vælg en gyldig fødselsdato.")


def birthdate_required(form, field):
    """Som DataRequired, men uden en ekstra fejl, når datoen allerede er afvist (fx 31. februar)."""
    if field.data is None:
        raise StopValidation(None if field.process_errors else "Vælg din fødselsdato.")


def _new_password_field(label="Password"):
    return PasswordField(
        label,
        validators=[
            DataRequired("Skriv et password."),
            Length(PASSWORD_MIN, PASSWORD_MAX, f"Passwordet skal være mellem {PASSWORD_MIN} og {PASSWORD_MAX} tegn."),
        ],
    )


def not_removed_marker(form, field):
    from ..moderation.content import REMOVED_NAME

    if (field.data or "").strip().lower() == REMOVED_NAME.lower():
        raise ValidationError("Vælg et andet navn.")


def _display_name_field():
    return StringField(
        "Vist navn",
        filters=[normalize_display_name],
        validators=[
            DataRequired("Skriv det navn, andre skal se."),
            Regexp(DISPLAY_NAME_PATTERN, message="2-30 tegn: bogstaver, tal, mellemrum, punktum, bindestreg eller apostrof."),
            contact_info.validator,
            name_rules.validator,
            not_removed_marker,
        ],
    )


class _Checkboxes(SelectMultipleField):
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


def gender_field():
    # Køn er påkrævet, fordi det bruges til at matche: hvem man vises for, og hvem der kan like en.
    return SelectField("Køn", choices=[("", "Vælg")] + choices.GENDERS,
                       validators=[DataRequired("Vælg dit køn. Det bruges til at vise dig for de rigtige.")])


def seeking_field():
    return _Checkboxes("Interesseret i", choices=choices.GENDERS, validate_choice=True)


class GenderForm(FlaskForm):
    """Til eksisterende profiler uden køn (fra før køn blev påkrævet)."""

    gender = gender_field()
    seeking_genders = seeking_field()


class DisplayNameForm(FlaskForm):
    """Ret det viste navn under Konto. Samme regler som ved oprettelsen."""

    display_name = _display_name_field()


class RegistrationForm(FlaskForm):
    display_name = _display_name_field()
    email = EmailField(
        "E-mail",
        filters=[_lower],
        validators=[DataRequired("Skriv din e-mail."), Email("Skriv en gyldig e-mail."), Length(max=254)],
    )
    password = _new_password_field()
    password_confirm = PasswordField(
        "Gentag password", validators=[DataRequired("Gentag passwordet."), EqualTo("password", "Passwordene er ikke ens.")]
    )
    birthdate = BirthdateField("Fødselsdato", validators=[birthdate_required])
    gender = gender_field()
    seeking_genders = seeking_field()
    postcode = StringField(
        "Postnummer",
        filters=[_strip],
        validators=[DataRequired("Skriv dit postnummer."), Regexp(r"^\d{4}$", message="Postnummeret skal være fire cifre.")],
    )
    # Vises kun, når postnummeret dækker flere kommuner (specifikation 5). Valgene sættes i view'et.
    kommune = SelectField("Kommune", choices=[], validate_choice=False)
    # Teksten med links står i skabelonen (auth/register.html).
    accept_terms = BooleanField(
        "Jeg er fyldt 18 år og accepterer brugervilkårene og privatlivspolitikken",
        validators=[DataRequired("Du skal acceptere vilkårene for at oprette en profil.")],
    )

    def validate_birthdate(self, field):
        today = today_local()
        if field.data > today:
            raise ValidationError("Fødselsdatoen kan ikke ligge i fremtiden.")
        if age_on(field.data, today) < MIN_AGE:
            raise ValidationError("Du skal være mindst 18 år for at oprette en profil.")
        if today.year - field.data.year > 120:
            raise ValidationError("Tjek fødselsdatoen.")

    def validate_postcode(self, field):
        # Præcis kontrol mod postnummerdatabasen kommer med lokationen (M3).
        if field.data and not 800 <= int(field.data) <= 9999:
            raise ValidationError("Det ligner ikke et dansk postnummer.")


class LoginForm(FlaskForm):
    email = EmailField("E-mail", filters=[_lower], validators=[DataRequired("Skriv din e-mail.")])
    password = PasswordField("Password", validators=[DataRequired("Skriv dit password.")])


class ForgotPasswordForm(FlaskForm):
    email = EmailField(
        "E-mail", filters=[_lower], validators=[DataRequired("Skriv din e-mail."), Email("Skriv en gyldig e-mail.")]
    )


class ResetPasswordForm(FlaskForm):
    password = _new_password_field("Nyt password")
    password_confirm = PasswordField(
        "Gentag nyt password", validators=[DataRequired("Gentag passwordet."), EqualTo("password", "Passwordene er ikke ens.")]
    )


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Nuværende password", validators=[DataRequired("Skriv dit nuværende password.")])
    password = _new_password_field("Nyt password")
    password_confirm = PasswordField(
        "Gentag nyt password", validators=[DataRequired("Gentag passwordet."), EqualTo("password", "Passwordene er ikke ens.")]
    )


class DeleteAccountForm(FlaskForm):
    password = PasswordField("Dit password", validators=[DataRequired("Skriv dit password for at bekræfte.")])
    confirm = BooleanField(
        "Jeg forstår, at min profil, mine billeder, likes og matches slettes, og at det ikke kan fortrydes",
        validators=[DataRequired("Bekræft, at du vil slette din profil.")],
    )


class ActionForm(FlaskForm):
    """Tom formular til knapper, der kun skal have CSRF-beskyttelse."""
