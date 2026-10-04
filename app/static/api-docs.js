"use strict";
fetch("/api/session")
  .then((r) => r.json())
  .then((session) => {
    window.ui = SwaggerUIBundle({
      url: "/openapi.json",
      dom_id: "#swagger-ui",
      deepLinking: true,
      validatorUrl: null,
      requestInterceptor: (req) => {
        req.headers["x-local-token"] = session.token;
        return req;
      },
      presets: [SwaggerUIBundle.presets.apis],
      layout: "BaseLayout",
    });
  });
