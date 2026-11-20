import { Injectable, OnModuleInit } from '@nestjs/common';

@Injectable()
export class GreeterService implements OnModuleInit {
  initialized = false;

  onModuleInit(): void {
    this.initialized = true;
  }

  greet(name: string): string {
    return `Hello, ${name}!`;
  }
}
