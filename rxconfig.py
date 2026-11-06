import reflex as rx

config = rx.Config(
    app_name="chipova_forge",
    plugins=[
        # Dark, like Chipova: dialogs and menus match the workspace.
        rx.plugins.RadixThemesPlugin(theme=rx.theme(appearance="dark", accent_color="amber", radius="medium")),
        rx.plugins.TailwindV3Plugin(
            config={
                "plugins": ["@tailwindcss/typography@0.5.20"],
                # Reflex 0.10 compiles memoized components into .web/app_components,
                # which the plugin's default content globs miss, so their classes
                # would never be generated.
                "content": [
                    "./app/**/*.{js,ts,jsx,tsx}",
                    "./utils/**/*.{js,ts,jsx,tsx}",
                    "./app_components/**/*.{js,ts,jsx,tsx}",
                ],
            },
        ),
    ],
    disable_plugins=[rx.plugins.SitemapPlugin],
)
