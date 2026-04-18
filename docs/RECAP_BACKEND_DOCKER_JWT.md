# Recap détaillé — Backend Spring Boot (Docker + JWT) — QoSentry

Date: 2026-04-10

## 1) Objectif et contexte

Ce document récapitule **tout ce qui a été mis en place** dans le dossier `backend/` pour obtenir :

- Un backend **Spring Boot (Java 17)** structuré en **N‑Tier**.
- Une authentification **stateless JWT** avec endpoints publics :
  - `POST /api/auth/register`
  - `POST /api/auth/login`
- Une exécution **100% Docker** (pas besoin d’avoir Maven installé en local) avec :
  - PostgreSQL
  - Redis
  - Backend Spring Boot
- Swagger/OpenAPI avec support **Bearer JWT** (bouton “Authorize”).

> Note Windows : des conflits de ports ont été rencontrés sur la machine hôte (notamment `8080` et `6379`). Le `docker-compose.yml` expose donc le backend en **8081** et Redis en **6380**.

---

## 2) Stack technique & décisions

### Backend

- **Spring Boot 3.3.4**, **Java 17**, **Maven**
- REST: `spring-boot-starter-web`
- Persistance: `spring-boot-starter-data-jpa` + `postgresql` (runtime)
- Redis: `spring-boot-starter-data-redis`
- Validation: `spring-boot-starter-validation`
- Swagger/OpenAPI: `springdoc-openapi-starter-webmvc-ui`

### Sécurité / JWT

- `spring-boot-starter-security`
- JWT: **JJWT 0.11.5** (`jjwt-api` + `jjwt-impl` + `jjwt-jackson`)

### Docker

- `docker-compose.yml` à la racine pour orchestrer Postgres/Redis/Backend.
- `backend/Dockerfile` en **multi-stage** : build Maven dans Docker → run JRE.

---

## 2bis) Chronologie des actions (ce qui a été fait)

1. **Scaffold Spring Boot (backend/)**

- Création du module Maven `backend/` avec Spring Boot 3.3.4, Java 17.
- Mise en place d’une arborescence N‑Tier (controllers/services/repositories/entities/dtos/config/exceptions).

2. **Ajout JWT stateless + Spring Security**

- Ajout des dépendances Spring Security + JJWT.
- Ajout des endpoints publics `/api/auth/register` et `/api/auth/login`.
- Ajout du filtre `JwtAuthenticationFilter` et des règles de sécurité dans `SecurityConfig`.
- Ajout du schéma Bearer dans Swagger/OpenAPI (`OpenApiConfig`).

3. **Docker-only workflow (sans Maven local)**

- Ajout d’un `backend/Dockerfile` multi-stage : build Maven dans Docker puis exécution JRE.
- Ajout d’un `docker-compose.yml` à la racine : Postgres 16 + Redis 7 + backend.

4. **Debug & stabilisation runtime Docker**

- Problème : dépendance circulaire Spring (entre config sécurité et filtre) lors du démarrage.
  - Correction : injection du filtre directement dans la méthode bean `securityFilterChain(...)`.
- Problème Windows : ports hôtes déjà occupés (ex: 8080, 6379).
  - Correction : mapping vers `8081:8080` pour le backend et `6380:6379` pour Redis.
- Problème JWT : secret non compatible Base64 (erreur `Illegal base64 character: '_'`).
  - Correction : `JwtService#getSigningKey()` accepte secret raw UTF‑8 ou Base64/Base64URL + fallback safe.

5. **Validation**

- Vérification que Swagger répond en HTTP 200 sur `http://localhost:8081/...`.
- Vérification de la persistance dans Postgres via `docker exec ... psql` (tables + lignes insérées).

## 3) Architecture N‑Tier (backend)

Le backend est organisé de façon classique :

- `controllers/` : endpoints REST
- `services/interfaces/` : contrats (interfaces)
- `services/implementations/` : implémentations
- `repositories/` : accès aux données (Spring Data)
- `entities/` : entités JPA
- `dtos/` : objets de transport (requêtes/réponses)
- `config/` : configuration (Security, OpenAPI, JWT)
- `exceptions/` : exceptions + handler global

---

## 4) Docker — orchestration et commandes nécessaires

### 4.1 Fichier docker-compose (source de vérité)

Le fichier utilisé est celui-ci (contenu exact actuel):

```yml
version: "3.9"

services:
  postgres:
    image: postgres:16
    container_name: qosentry-postgres
    environment:
      POSTGRES_USER: admin
      POSTGRES_PASSWORD: admin
      POSTGRES_DB: qosentry
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U admin -d qosentry"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7
    container_name: qosentry-redis
    ports:
      - "6380:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    container_name: qosentry-backend
    ports:
      - "8081:8080"
    environment:
      SPRING_DATASOURCE_URL: jdbc:postgresql://postgres:5432/qosentry
      SPRING_DATASOURCE_USERNAME: admin
      SPRING_DATASOURCE_PASSWORD: admin
      SPRING_DATA_REDIS_HOST: redis
      SPRING_DATA_REDIS_PORT: 6379
      APP_JWT_SECRET: qosentry_jwt_secret_dev_2026_secure
      APP_JWT_EXPIRATION_MS: 86400000
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy

volumes:
  postgres_data:
```

> Remarque : Docker Compose v2 affiche un warning `version is obsolete`. Ce champ est ignoré par Compose v2 ; ce n’est pas bloquant.

### 4.2 Dockerfile backend (build Maven dans Docker)

Contenu exact actuel :

```dockerfile
# Stage 1: build the jar using Maven inside Docker
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /app
COPY pom.xml .
RUN mvn dependency:go-offline -B
COPY src ./src
RUN mvn clean package -DskipTests

# Stage 2: run the jar on a slim JRE image
FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=build /app/target/backend-0.0.1-SNAPSHOT.jar app.jar
ENTRYPOINT ["java", "-jar", "app.jar"]
```

### 4.2bis .dockerignore (optimisation du build)

Contenu exact actuel :

```gitignore
target/
*.iml
.idea/
.vscode/
*.log
```

### 4.3 Commandes Docker (workflow)

Depuis la racine du repo :

- Démarrer (build + run) :
  - `docker compose up -d --build`
- Voir l’état :
  - `docker compose ps`
  - `docker ps`
- Logs :
  - `docker compose logs -f backend`
  - `docker compose logs -f postgres`
  - `docker compose logs -f redis`
- Stop (garder les données) :
  - `docker compose down`
- Reset complet (supprimer volumes/données Postgres) :
  - `docker compose down -v`

### 4.4 Commandes d’inspection “contenu des conteneurs”

- Entrer dans Postgres :
  - `docker exec -it qosentry-postgres psql -U admin -d qosentry`
- Lister les tables :
  - `docker exec qosentry-postgres psql -U admin -d qosentry -c "\\dt"`
- Inspecter la table users :
  - `docker exec qosentry-postgres psql -U admin -d qosentry -c "\\d users"`
- Vérifier Redis :
  - `docker exec qosentry-redis redis-cli ping`

---

## 5) Configuration Spring (YAML + variables d’environnement)

### 5.1 application.yml (local/dev)

Contenu exact actuel :

```yml
spring:
  application:
    name: qosentry-backend

  main:
    allow-circular-references: true

  datasource:
    url: jdbc:postgresql://localhost:5432/qosentry
    username: admin
    password: admin
    driver-class-name: org.postgresql.Driver

  jpa:
    hibernate:
      ddl-auto: update
    show-sql: true
    properties:
      hibernate:
        dialect: org.hibernate.dialect.PostgreSQLDialect

  data:
    redis:
      host: localhost
      port: 6379

springdoc:
  swagger-ui:
    path: /swagger-ui.html

server:
  port: ${SERVER_PORT:8080}

app:
  jwt:
    secret: CHANGE_ME_32_CHAR_SECRET_KEY_HERE
    expiration-ms: 86400000
```

Important :

- En **Docker**, ce YAML est surchargé par les variables du `docker-compose.yml` :
  - `SPRING_DATASOURCE_URL`, `SPRING_DATA_REDIS_HOST`, etc.
  - `APP_JWT_SECRET` → bind vers `app.jwt.secret`
  - `APP_JWT_EXPIRATION_MS` → bind vers `app.jwt.expiration-ms`

### 5.2 application-dev.yml

Contenu exact actuel :

```yml
spring:
  config:
    activate:
      on-profile: dev

  jpa:
    hibernate:
      ddl-auto: update

logging:
  level:
    org.springframework: INFO
    org.hibernate.SQL: DEBUG
```

---

## 6) JWT — principe + logique exacte implémentée

### 6.1 Principe (stateless)

- Le backend **ne maintient pas de session serveur**.
- Le client s’authentifie via `/api/auth/login` (ou s’inscrit via `/api/auth/register`).
- Le backend renvoie un **JWT**.
- Le client envoie ensuite ce JWT sur chaque requête protégée via :

`Authorization: Bearer <TOKEN>`

### 6.2 Contenu minimal du JWT (dans cette implémentation)

- `sub` (subject) : `username`
- `iat` : date d’émission
- `exp` : date d’expiration
- Signature : **HS256** (HMAC-SHA256) avec le secret `app.jwt.secret`

### 6.3 Génération du token (JwtService)

Extrait complet du service (contenu exact actuel):

```java
package com.project.backend.config;

import io.jsonwebtoken.Claims;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;
import io.jsonwebtoken.io.Decoders;
import io.jsonwebtoken.security.Keys;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.security.Key;
import java.util.Date;

@Component
public class JwtService {

    @Value("${app.jwt.secret}")
    private String secret;

    @Value("${app.jwt.expiration-ms}")
    private long expirationMs;

    public String generateToken(UserDetails user) {
        Date now = new Date();
        Date expiry = new Date(now.getTime() + expirationMs);

        return Jwts.builder()
                .setSubject(user.getUsername())
                .setIssuedAt(now)
                .setExpiration(expiry)
                .signWith(getSigningKey(), SignatureAlgorithm.HS256)
                .compact();
    }

    public String extractUsername(String token) {
        return extractAllClaims(token).getSubject();
    }

    public boolean isTokenValid(String token, UserDetails user) {
        String username = extractUsername(token);
        return username.equals(user.getUsername()) && !isTokenExpired(token);
    }

    private boolean isTokenExpired(String token) {
        return extractAllClaims(token).getExpiration().before(new Date());
    }

    private Claims extractAllClaims(String token) {
        return Jwts.parserBuilder()
                .setSigningKey(getSigningKey())
                .build()
                .parseClaimsJws(token)
                .getBody();
    }

    private Key getSigningKey() {
        // Accept either:
        // - a raw secret string (recommended for local/dev)
        // - a Base64/Base64URL-encoded secret (common in CI/secrets managers)
        byte[] rawBytes = secret == null ? new byte[0] : secret.getBytes(StandardCharsets.UTF_8);
        byte[] keyBytes = rawBytes;
        String trimmed = secret == null ? "" : secret.trim();

        // Only try decoding when the value looks like an encoded blob.
        if (trimmed.length() >= 32) {
            try {
                // Standard Base64 (A-Z a-z 0-9 + / =)
                if (trimmed.matches("^[A-Za-z0-9+/=]+$")) {
                    byte[] decoded = Decoders.BASE64.decode(trimmed);
                    if (decoded.length >= 32) {
                        keyBytes = decoded;
                    }
                }
                // URL-safe Base64 (A-Z a-z 0-9 - _ =)
                else if (trimmed.matches("^[A-Za-z0-9_\\-=]+$")) {
                    byte[] decoded = Decoders.BASE64URL.decode(trimmed);
                    if (decoded.length >= 32) {
                        keyBytes = decoded;
                    }
                }
            } catch (RuntimeException ignored) {
                // Fall back to raw UTF-8 bytes.
                keyBytes = trimmed.getBytes(StandardCharsets.UTF_8);
            }
        }

        try {
            return Keys.hmacShaKeyFor(keyBytes);
        } catch (RuntimeException ex) {
            // If a decode produced a weak key, fall back to raw bytes.
            if (keyBytes != rawBytes && rawBytes.length >= 32) {
                return Keys.hmacShaKeyFor(rawBytes);
            }
            throw ex;
        }
    }
}
```

Pourquoi cette logique de secret ?

- Un bug initial est arrivé quand le secret contenait `_` (décodage Base64).
- La version actuelle accepte :
  - Secret **raw** (UTF‑8) recommandé en local
  - Secret **Base64/Base64URL** (si fourni par un vault/CI)
- Et évite d’utiliser une clé “trop courte” (minimum 32 bytes pour HS256).

### 6.4 Filtre JWT (lecture Authorization + SecurityContext)

Contenu exact actuel :

```java
package com.project.backend.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.web.authentication.WebAuthenticationDetailsSource;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;

@Component
@RequiredArgsConstructor
public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private final JwtService jwtService;
    private final UserDetailsService userDetailsService;

    @Override
    protected void doFilterInternal(
            HttpServletRequest request,
            HttpServletResponse response,
            FilterChain filterChain) throws ServletException, IOException {
        String authHeader = request.getHeader("Authorization");

        if (authHeader == null || !authHeader.startsWith("Bearer ")) {
            filterChain.doFilter(request, response);
            return;
        }

        String jwt = authHeader.substring(7);

        String username;
        try {
            username = jwtService.extractUsername(jwt);
        } catch (Exception ex) {
            filterChain.doFilter(request, response);
            return;
        }

        if (username != null && SecurityContextHolder.getContext().getAuthentication() == null) {
            UserDetails userDetails = userDetailsService.loadUserByUsername(username);

            if (jwtService.isTokenValid(jwt, userDetails)) {
                UsernamePasswordAuthenticationToken authToken = new UsernamePasswordAuthenticationToken(userDetails,
                        null, userDetails.getAuthorities());
                authToken.setDetails(new WebAuthenticationDetailsSource().buildDetails(request));
                SecurityContextHolder.getContext().setAuthentication(authToken);
            }
        }

        filterChain.doFilter(request, response);
    }
}
```

### 6.5 Règles de sécurité (SecurityConfig)

Contenu exact actuel :

```java
package com.project.backend.config;

import com.project.backend.repositories.UserRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.config.annotation.authentication.configuration.AuthenticationConfiguration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

@Configuration
@EnableWebSecurity
@RequiredArgsConstructor
public class SecurityConfig {

    private final UserRepository userRepository;

    @Bean
    public SecurityFilterChain securityFilterChain(HttpSecurity http, JwtAuthenticationFilter jwtAuthenticationFilter)
            throws Exception {
        http
                .csrf(csrf -> csrf.disable())
                .sessionManagement(sm -> sm.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/api/auth/**").permitAll()
                        .requestMatchers("/swagger-ui/**", "/swagger-ui.html").permitAll()
                        .requestMatchers("/v3/api-docs/**").permitAll()
                        .anyRequest().authenticated())
                .addFilterBefore(jwtAuthenticationFilter, UsernamePasswordAuthenticationFilter.class);

        return http.build();
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    @Bean
    public UserDetailsService userDetailsService() {
        return username -> userRepository.findByUsername(username)
                .orElseThrow(() -> new UsernameNotFoundException("User not found: " + username));
    }

    @Bean
    public AuthenticationManager authenticationManager(AuthenticationConfiguration config) throws Exception {
        return config.getAuthenticationManager();
    }
}
```

### 6.6 Endpoints d’auth (controller + service)

Controller actuel :

```java
package com.project.backend.controllers;

import com.project.backend.dtos.AuthResponseDto;
import com.project.backend.dtos.LoginRequestDto;
import com.project.backend.dtos.RegisterRequestDto;
import com.project.backend.services.interfaces.AuthService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/auth")
@RequiredArgsConstructor
public class AuthController {

    private final AuthService authService;

    @PostMapping("/register")
    public ResponseEntity<AuthResponseDto> register(@Valid @RequestBody RegisterRequestDto request) {
        return ResponseEntity.ok(authService.register(request));
    }

    @PostMapping("/login")
    public ResponseEntity<AuthResponseDto> login(@Valid @RequestBody LoginRequestDto request) {
        return ResponseEntity.ok(authService.login(request));
    }
}
```

Service actuel (logique register/login) :

```java
package com.project.backend.services.implementations;

import com.project.backend.config.JwtService;
import com.project.backend.dtos.AuthResponseDto;
import com.project.backend.dtos.LoginRequestDto;
import com.project.backend.dtos.RegisterRequestDto;
import com.project.backend.entities.User;
import com.project.backend.repositories.UserRepository;
import com.project.backend.services.interfaces.AuthService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

@Service
@RequiredArgsConstructor
public class AuthServiceImpl implements AuthService {

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final JwtService jwtService;
    private final AuthenticationManager authenticationManager;

    @Override
    public AuthResponseDto register(RegisterRequestDto request) {
        if (userRepository.findByUsername(request.getUsername()).isPresent()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Username already in use");
        }
        if (userRepository.findByEmail(request.getEmail()).isPresent()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Email already in use");
        }

        User user = User.builder()
                .username(request.getUsername())
                .email(request.getEmail())
                .password(passwordEncoder.encode(request.getPassword()))
                .role(User.Role.USER)
                .build();

        User saved = userRepository.save(user);
        String token = jwtService.generateToken(saved);

        return AuthResponseDto.builder()
                .token(token)
                .username(saved.getUsername())
                .role(saved.getRole().name())
                .build();
    }

    @Override
    public AuthResponseDto login(LoginRequestDto request) {
        authenticationManager.authenticate(
                new UsernamePasswordAuthenticationToken(request.getUsername(), request.getPassword()));

        User user = userRepository.findByUsername(request.getUsername())
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.UNAUTHORIZED, "Invalid credentials"));

        String token = jwtService.generateToken(user);

        return AuthResponseDto.builder()
                .token(token)
                .username(user.getUsername())
                .role(user.getRole().name())
                .build();
    }
}
```

### 6.6bis DTOs (requêtes/réponses)

RegisterRequestDto :

```java
package com.project.backend.dtos;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class RegisterRequestDto {

  @NotBlank
  private String username;

  @NotBlank
  @Email
  private String email;

  @NotBlank
  private String password;
}
```

LoginRequestDto :

```java
package com.project.backend.dtos;

import jakarta.validation.constraints.NotBlank;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class LoginRequestDto {

  @NotBlank
  private String username;

  @NotBlank
  private String password;
}
```

AuthResponseDto :

```java
package com.project.backend.dtos;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class AuthResponseDto {
  private String token;
  private String username;
  private String role;
}
```

### 6.6ter Modèle utilisateur + repository

User (entité + UserDetails) :

```java
package com.project.backend.entities;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.NoArgsConstructor;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.userdetails.UserDetails;

import java.util.Collection;
import java.util.List;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@EqualsAndHashCode(callSuper = true)
@Entity
@Table(name = "users")
public class User extends BaseEntity implements UserDetails {

  @Column(nullable = false, unique = true)
  private String username;

  @Column(nullable = false, unique = true)
  private String email;

  @Column(nullable = false)
  private String password;

  @Enumerated(EnumType.STRING)
  @Column(nullable = false)
  private Role role;

  public enum Role {
    USER,
    ADMIN
  }

  @Override
  public Collection<? extends GrantedAuthority> getAuthorities() {
    return List.of(new SimpleGrantedAuthority("ROLE_" + role.name()));
  }

  @Override
  public boolean isAccountNonExpired() {
    return true;
  }

  @Override
  public boolean isAccountNonLocked() {
    return true;
  }

  @Override
  public boolean isCredentialsNonExpired() {
    return true;
  }

  @Override
  public boolean isEnabled() {
    return true;
  }
}
```

UserRepository :

```java
package com.project.backend.repositories;

import com.project.backend.entities.User;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;
import java.util.UUID;

public interface UserRepository extends JpaRepository<User, UUID> {
  Optional<User> findByUsername(String username);

  Optional<User> findByEmail(String email);
}
```

### 6.7 Swagger + Bearer JWT

Le Swagger est configuré pour exposer un schéma Bearer nommé `bearerAuth`.

Contenu exact actuel :

```java
package com.project.backend.config;

import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.Components;
import io.swagger.v3.oas.models.info.Info;
import io.swagger.v3.oas.models.security.SecurityRequirement;
import io.swagger.v3.oas.models.security.SecurityScheme;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class OpenApiConfig {

    @Bean
    public OpenAPI openAPI() {
        final String schemeName = "bearerAuth";
        return new OpenAPI()
                .addSecurityItem(new SecurityRequirement().addList(schemeName))
                .components(new Components().addSecuritySchemes(
                        schemeName,
                        new SecurityScheme()
                                .name(schemeName)
                                .type(SecurityScheme.Type.HTTP)
                                .scheme("bearer")
                                .bearerFormat("JWT")))
                .info(new Info()
                        .title("QoSentry API")
                        .version("v1")
                        .description("QoSentry backend REST API"));
    }
}
```

---

## 7) “Contenu” des conteneurs — état observé sur la machine

Lors de la dernière vérification (2026-04-10), les conteneurs `qosentry-*` existent mais sont **arrêtés** :

```text
qosentry-backend   Exited (143)
qosentry-postgres  Exited (0)
qosentry-redis     Exited (0)
```

Inspection (sans secrets) :

```text
qosentry-postgres: image=postgres:16, status=exited, volume=esprit-pi-4ds9-2526-qosentry_postgres_data -> /var/lib/postgresql/data
qosentry-redis:    image=redis:7, status=exited
qosentry-backend:  image=esprit-pi-4ds9-2526-qosentry-backend, status=exited
```

> Les ports ne sont pas listés dans `docker inspect` quand le conteneur est stoppé ; la source de vérité des ports exposés est `docker-compose.yml`.

---

## 8) Vérifications rapides (API)

Quand le stack est up, l’API est accessible via :

- Swagger UI : `http://localhost:8081/swagger-ui/index.html`
- OpenAPI JSON : `http://localhost:8081/v3/api-docs`

Exemples avec `curl` :

- Register :

```bash
curl -X POST "http://localhost:8081/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{"username":"test","email":"test@example.com","password":"pass1234"}'
```

- Login :

```bash
curl -X POST "http://localhost:8081/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"test","password":"pass1234"}'
```

- Appel protégé (exemple) :

```bash
curl -X GET "http://localhost:8081/api/secure" \
  -H "Authorization: Bearer <TOKEN>"
```

---

## 9) Notes / points d’attention

- `spring.main.allow-circular-references: true` est présent dans `application.yml`.
  - Il avait été utilisé comme “filet de sécurité” pendant le debug de dépendances Spring.
  - Aujourd’hui, `SecurityConfig` injecte le filtre dans la méthode `securityFilterChain(...)`, ce qui casse le cycle.
  - Recommandation (optionnelle) : le retirer si vous voulez éviter d’autoriser des cycles par défaut.

- Secrets JWT : évitez de commiter des secrets réels. En production, injecter via un gestionnaire de secrets.

---

## 10) Dépendances Maven (pom.xml)

Pour référence, le `pom.xml` actuel contient notamment : Spring Web/JPA/Redis/Validation/Security, springdoc OpenAPI, et JJWT 0.11.5.

(Le fichier est consultable dans `backend/pom.xml`.)
