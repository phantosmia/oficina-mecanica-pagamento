# Banco próprio do serviço (ADR-0009: um banco por serviço, sem compartilhar
# instância), dentro da VPC de banco que já existe
# (oficina-mecanica-infra-banco-dados), sem criar VPC nova. Os pods chegam
# até ele pelo VPC Peering criado em oficina-mecanica-infra-kubernetes.
data "terraform_remote_state" "database" {
  backend = "s3"

  config = {
    bucket = var.tf_state_bucket
    key    = "database/${var.environment}/terraform.tfstate"
    region = var.tf_state_region
  }
}

resource "aws_db_subnet_group" "postgres" {
  name       = "${local.prefix}-pagamento"
  subnet_ids = data.terraform_remote_state.database.outputs.private_subnet_ids
}

resource "aws_security_group" "postgres" {
  name        = "${local.prefix}-pagamento-postgres"
  description = "PostgreSQL do Pagamento a partir da VPC do EKS"
  vpc_id      = data.terraform_remote_state.database.outputs.vpc_id

  ingress {
    description = "PostgreSQL a partir da VPC do EKS (via VPC Peering)"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [data.terraform_remote_state.kubernetes.outputs.vpc_cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# Só letras e números: a senha vai para a URL de conexão.
resource "random_password" "postgres" {
  length  = 24
  special = false
}

resource "aws_db_instance" "postgres" {
  identifier = "${local.prefix}-pagamento"

  engine         = "postgres"
  engine_version = var.rds_engine_version
  instance_class = var.rds_instance_class

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = "oficina_pagamento"
  username = "pagamento"
  password = random_password.postgres.result

  db_subnet_group_name   = aws_db_subnet_group.postgres.name
  vpc_security_group_ids = [aws_security_group.postgres.id]
  publicly_accessible    = false

  backup_retention_period    = var.rds_backup_retention_period
  deletion_protection        = false
  skip_final_snapshot        = true
  apply_immediately          = true
  auto_minor_version_upgrade = true
}
