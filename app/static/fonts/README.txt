Drop woff2 files here to run without reaching out to Google Fonts.

  1. Visit the Google Fonts CSS URL in the <link> at the top of index.html
     from any machine with internet, and save the two woff2 files it points at
     as big-shoulders.woff2 and saira-condensed.woff2.
  2. In index.html, delete the three <link ...fonts...> lines and uncomment
     the @font-face block directly below them.

The display falls back to condensed system fonts if neither is available, so
this is optional — it just looks better with them.
