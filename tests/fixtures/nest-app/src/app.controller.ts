import { Controller, Get } from '@nestjs/common';

import { GreeterService } from './greeter.service';

@Controller()
export class AppController {
  constructor(private readonly greeter: GreeterService) {}

  @Get()
  hello(): string {
    return this.greeter.greet('HTTP');
  }
}
