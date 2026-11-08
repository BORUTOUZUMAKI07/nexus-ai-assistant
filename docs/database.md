# Database Schema

## Overview

Nexus uses PostgreSQL as the primary database with pgvector for embeddings. Core entities are modeled with SQLModel.

## Entity Relationship Diagram

`mermaid
erDiagram
    USER ||--o{ CONVERSATION : owns
    USER ||--o{ MESSAGE : creates
    USER ||--o{ ARTIFACT : owns
    USER ||--o{ USAGE_LOG : generates
    USER ||--o{ API_KEY : has
    CONVERSATION ||--o{ MESSAGE : contains
    CONVERSATION ||--o{ RUN : has
    RUN ||--o{ RUN_EVENT : emits
    ORGANIZATION ||--o{ USER : contains
    ORGANIZATION ||--o{ USAGE_LOG : aggregates
    KNOWLEDGE_DOCUMENT ||--o{ CHUNK : contains
    CHUNK ||--o{ EMBEDDING : has

    USER {
        uuid id PK
        string email UK
        string username
        string role
        boolean is_active
        boolean is_verified
        timestamp created_at
        timestamp updated_at
    }

    CONVERSATION {
        uuid id PK
        uuid user_id FK
        uuid organization_id FK
        string title
        string model
        string system_prompt
        timestamp created_at
        timestamp updated_at
    }

    MESSAGE {
        uuid id PK
        uuid conversation_id FK
        uuid user_id FK
        string role
        json content
        json metadata
        timestamp created_at
    }

    RUN {
        uuid id PK
        uuid conversation_id FK
        uuid user_id FK
        string status
        json config
        json result
        timestamp started_at
        timestamp completed_at
    }

    RUN_EVENT {
        uuid id PK
        uuid run_id FK
        string event_type
        json data
        timestamp created_at
    }

    ARTIFACT {
        uuid id PK
        uuid user_id FK
        uuid conversation_id FK
        string type
        string name
        json content
        json metadata
        timestamp created_at
    }

    ORGANIZATION {
        uuid id PK
        string name
        string slug UK
        json settings
        timestamp created_at
    }

    USAGE_LOG {
        uuid id PK
        uuid user_id FK
        uuid organization_id FK
        string model
        int prompt_tokens
        int completion_tokens
        float cost_usd
        timestamp created_at
    }

    API_KEY {
        uuid id PK
        uuid user_id FK
        string name
        string key_hash
        json scopes
        timestamp expires_at
        timestamp last_used_at
        timestamp created_at
    }

    KNOWLEDGE_DOCUMENT {
        uuid id PK
        uuid user_id FK
        string filename
        string content_type
        int size_bytes
        string status
        timestamp created_at
    }

    CHUNK {
        uuid id PK
        uuid document_id FK
        text content
        json metadata
        int chunk_index
        timestamp created_at
    }

    EMBEDDING {
        uuid id PK
        uuid chunk_id FK
        vector embedding
        string model
        timestamp created_at
    }
`

## Migrations

Database schema is managed with [Alembic](https://alembic.sqlalchemy.org/). Migrations are in ackend/alembic/versions/.

To apply migrations:
`ash
cd backend
uv run alembic upgrade head
`
