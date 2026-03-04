# nestjs/nest

| Field | Value |
|-------|-------|
| **URL** | https://github.com/nestjs/nest |
| **License** | MIT |
| **Language** | TypeScript |
| **Scale** | Large (multi-team project) |
| **Category** | Enterprise server-side framework |

## Why this repo

- **No single developer knows it all**: Core DI container, HTTP platform
  adapters (Express/Fastify), WebSockets, microservices (TCP, Redis, NATS,
  MQTT, gRPC, Kafka, RabbitMQ), GraphQL integration, scheduling, caching,
  testing utilities, CLI — each a substantial, independently complex subsystem.
- **Well-structured**: Monorepo with `packages/` containing clearly separated
  modules (`@nestjs/core`, `@nestjs/common`, `@nestjs/microservices`,
  `@nestjs/websockets`, `@nestjs/testing`, `@nestjs/platform-express`, etc.).
  Each package has its own internal module hierarchy.
- **Rich history**: 8K+ commits, active PR flow with detailed reviews.
  Enterprise adoption drives realistic, varied development patterns.
- **Permissive**: MIT license.

## Structure overview

```
packages/
├── core/                # DI container, module system, lifecycle hooks
│   ├── injector/        # Dependency injection engine
│   ├── router/          # Route resolution and execution
│   ├── middleware/       # Middleware pipeline
│   ├── interceptors/    # Interceptor chain
│   ├── guards/          # Guard execution
│   └── pipes/           # Pipe transformation
├── common/              # Decorators, interfaces, utilities
├── microservices/       # Transport strategies (TCP, Redis, NATS, gRPC, Kafka)
├── websockets/          # WebSocket gateway
├── platform-express/    # Express HTTP adapter
├── platform-fastify/    # Fastify HTTP adapter
├── testing/             # Test module and utilities
└── platform-socket.io/  # Socket.IO adapter
```

## Scale indicators

- ~500 TypeScript source files across packages
- ~80K+ lines of code
- Deep module hierarchies (3-5 levels)
- Cross-cutting DI, lifecycle, and middleware concerns
