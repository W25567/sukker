from ..extensions import db
from ..media import storage
from ..media.services import list_images


def delete_account(user):
    """Sletter kontoen og alle profildata (specifikation 13.1 og 7.2).

    Databasen sletter profil, billeder, likes, matches, abonnementer og sessioner. Beskeder, brugeren har
    sendt, bliver stående hos modparten, hvor afsenderen vises som "Slettet bruger", fordi deres
    afsender sættes til NULL. Billedfilerne slettes fra disken her.
    """
    images = [image for collection in ("public", "private") for image in list_images(user.id, collection)]
    db.session.delete(user)
    db.session.flush()
    for image in images:
        storage.delete_files(image)
