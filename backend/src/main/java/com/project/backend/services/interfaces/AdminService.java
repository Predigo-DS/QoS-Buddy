package com.project.backend.services.interfaces;

import com.project.backend.dtos.UserDto;

import java.util.List;
import java.util.UUID;

public interface AdminService {
    List<UserDto> getAllUsers();
    UserDto updateRole(UUID userId, String role);
    void deleteUser(UUID userId);
}
