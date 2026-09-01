"""The routes, split by the surface they answer rather than by the entity they touch.

``grades`` and ``api`` cover the same use cases and differ only in rendering and credential;
``auth`` covers the one thing that happens before any use case. Splitting by surface is what
keeps the pairing visible -- the two grade routers sit side by side, and a rule that appeared in
one of them and not the other would be obvious in a way it would not be if they were filed under
"grades" together.
"""
