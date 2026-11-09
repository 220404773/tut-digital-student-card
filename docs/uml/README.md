# UML models (final system)

Editable PlantUML sources (`.puml`) with the rendered images (`.png`) used in the Assignment 5 slides.

| Diagram | Source | Shows |
|---|---|---|
| Use case | `usecase.puml` | Actors (Student, Verifier, Administrator, AI Verification Service, Email Service, Google Wallet API) and the use cases; green = demonstrated |
| Activity | `activity.puml` | Signup to a live card, with the AI step, decisions and the staff-review path |
| Class | `class.puml` | Entity classes (tables) and control classes (services), with multiplicities |
| Sequence | `sequence.puml` | Signup across UI, application logic, the AI service and the database |
| Deployment | `deployment.puml` | DigitalOcean App Platform, PostgreSQL, Spaces, Azure OpenAI, Azure email, Google Wallet, and the offline laptop copy |

Edit a `.puml` file in any PlantUML editor (for example the VS Code PlantUML extension or plantuml.com), or render it with:

```
java -jar plantuml.jar -tpng -Sdpi=160 docs/uml/*.puml
```
