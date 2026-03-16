"""Template files for recon init and other commands."""

from coderecon.core.excludes import generate_reconignore_template


def get_reconignore_template() -> str:
    """Get the default .reconignore template."""
    return generate_reconignore_template()


__all__ = ["get_reconignore_template"]
