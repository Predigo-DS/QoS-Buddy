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
