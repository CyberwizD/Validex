import reflex as rx

config = rx.Config(
    app_name="Validex",
    state_auto_setters=True,
    plugins=[
        rx.plugins.SitemapPlugin(),
        rx.plugins.TailwindV4Plugin(),
    ]
)
