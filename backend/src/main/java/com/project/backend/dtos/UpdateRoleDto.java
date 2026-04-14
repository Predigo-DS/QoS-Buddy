package com.project.backend.dtos;

import jakarta.validation.constraints.NotNull;
import lombok.Data;

@Data
public class UpdateRoleDto {
    @NotNull
    private String role; // "USER" or "ADMIN"
}
