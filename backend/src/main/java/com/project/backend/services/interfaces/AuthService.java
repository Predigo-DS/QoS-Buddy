package com.project.backend.services.interfaces;

import com.project.backend.dtos.AuthResponseDto;
import com.project.backend.dtos.LoginRequestDto;
import com.project.backend.dtos.RegisterRequestDto;

public interface AuthService {
    AuthResponseDto register(RegisterRequestDto request);

    AuthResponseDto login(LoginRequestDto request);
}
